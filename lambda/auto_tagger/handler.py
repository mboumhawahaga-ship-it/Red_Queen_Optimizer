"""
🏷️  AUTO-TAGGER — Red Queen Governance
Écoute les événements CloudTrail via EventBridge et applique
automatiquement les tags manquants sur les ressources nouvellement créées.

Événements traités :
  EC2   → RunInstances
  S3    → CreateBucket
  RDS   → CreateDBInstance
  Lambda→ CreateFunction

Comportement :
  - DRY_RUN=true  : log ce qui serait taggé, n'écrit rien sur AWS
  - DRY_RUN=false : applique les tags manquants + persiste dans DynamoDB
"""

import os
import json
import time
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Configuration ────────────────────────────────────────────────────────────
DRY_RUN             = os.environ.get("DRY_RUN", "true").lower() == "true"
DYNAMODB_TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME", "")
DEFAULT_ENVIRONMENT = os.environ.get("DEFAULT_ENVIRONMENT", "dev")
DEFAULT_SQUAD       = os.environ.get("DEFAULT_SQUAD", "unknown")
DEFAULT_COST_CENTER = os.environ.get("DEFAULT_COST_CENTER", "CC-000")
DEFAULT_OWNER       = os.environ.get("DEFAULT_OWNER", "auto-tagger@entreprise.com")

REQUIRED_TAGS = ["Owner", "Squad", "CostCenter", "Environment"]

# Événements CloudTrail → (service, extracteur d'ARN/ID)
WATCHED_EVENTS: Dict[str, str] = {
    "RunInstances":    "ec2",
    "CreateBucket":    "s3",
    "CreateDBInstance": "rds",
    "CreateFunction20150331": "lambda",
}

# ── Clients AWS ───────────────────────────────────────────────────────────────
ec2_client    = boto3.client("ec2")
s3_client     = boto3.client("s3")
rds_client    = boto3.client("rds")
lambda_client = boto3.client("lambda")
dynamodb      = boto3.resource("dynamodb")


# ═════════════════════════════════════════════════════════════════════════════
# POINT D'ENTRÉE
# ═════════════════════════════════════════════════════════════════════════════

def lambda_handler(event: Dict, context: Any) -> Dict:
    logger.info("Auto-tagger démarré — DRY_RUN=%s", DRY_RUN)
    logger.debug("Event reçu : %s", json.dumps(event, default=str))

    detail      = event.get("detail", {})
    event_name  = detail.get("eventName", "")
    service     = WATCHED_EVENTS.get(event_name)

    if not service:
        logger.info("Événement ignoré : %s", event_name)
        return {"statusCode": 200, "body": json.dumps({"skipped": event_name})}

    result = _dispatch(service, event_name, detail)

    return {"statusCode": 200, "body": json.dumps(result, default=str)}


# ═════════════════════════════════════════════════════════════════════════════
# DISPATCH PAR SERVICE
# ═════════════════════════════════════════════════════════════════════════════

def _dispatch(service: str, event_name: str, detail: Dict) -> Dict:
    handlers = {
        "ec2":    _handle_ec2,
        "s3":     _handle_s3,
        "rds":    _handle_rds,
        "lambda": _handle_lambda,
    }
    return handlers[service](detail)


def _handle_ec2(detail: Dict) -> Dict:
    items = (detail.get("responseElements", {})
                   .get("instancesSet", {})
                   .get("items", []))
    results = []
    for item in items:
        instance_id = item.get("instanceId")
        if not instance_id:
            continue
        existing = _get_ec2_tags(instance_id)
        defaults = _build_defaults(detail, instance_id, "ec2")
        missing  = _compute_missing_tags(existing, defaults)
        results.append(_apply_tags(
            resource_id=instance_id,
            resource_type="EC2",
            existing_tags=existing,
            missing_tags_to_apply=missing,
            tagger=lambda tags: ec2_client.create_tags(
                Resources=[instance_id],
                Tags=[{"Key": k, "Value": v} for k, v in tags.items()]
            ),
        ))
    return {"ec2": results}


def _handle_s3(detail: Dict) -> Dict:
    bucket_name = (detail.get("requestParameters", {})
                         .get("bucketName", ""))
    if not bucket_name:
        return {"s3": []}

    existing = _get_s3_tags(bucket_name)
    defaults = _build_defaults(detail, bucket_name, "s3")
    missing  = _compute_missing_tags(existing, defaults)
    result   = _apply_tags(
        resource_id=bucket_name,
        resource_type="S3",
        existing_tags=existing,
        missing_tags_to_apply=missing,
        tagger=lambda tags: s3_client.put_bucket_tagging(
            Bucket=bucket_name,
            Tagging={"TagSet": [{"Key": k, "Value": v} for k, v in tags.items()]}
        ),
    )
    return {"s3": [result]}


def _handle_rds(detail: Dict) -> Dict:
    db_id = (detail.get("requestParameters", {})
                   .get("dBInstanceIdentifier", ""))
    if not db_id:
        return {"rds": []}

    # L'ARN RDS n'est pas dans l'event — on le reconstruit
    region     = detail.get("awsRegion", os.environ.get("AWS_REGION", "eu-west-1"))
    account_id = detail.get("userIdentity", {}).get("accountId", "")
    db_arn     = f"arn:aws:rds:{region}:{account_id}:db:{db_id}"

    existing = _get_rds_tags(db_arn)
    defaults = _build_defaults(detail, db_id, "rds")
    missing  = _compute_missing_tags(existing, defaults)
    result   = _apply_tags(
        resource_id=db_id,
        resource_type="RDS",
        existing_tags=existing,
        missing_tags_to_apply=missing,
        tagger=lambda tags: rds_client.add_tags_to_resource(
            ResourceName=db_arn,
            Tags=[{"Key": k, "Value": v} for k, v in tags.items()]
        ),
    )
    return {"rds": [result]}


def _handle_lambda(detail: Dict) -> Dict:
    func_name = (detail.get("requestParameters", {})
                       .get("functionName", ""))
    if not func_name:
        return {"lambda": []}

    func_arn  = (detail.get("responseElements", {})
                       .get("functionArn", ""))
    existing  = _get_lambda_tags(func_arn) if func_arn else {}
    defaults  = _build_defaults(detail, func_name, "lambda")
    missing   = _compute_missing_tags(existing, defaults)
    result    = _apply_tags(
        resource_id=func_name,
        resource_type="Lambda",
        existing_tags=existing,
        missing_tags_to_apply=missing,
        tagger=lambda tags: lambda_client.tag_resource(
            Resource=func_arn,
            Tags=tags
        ) if func_arn else None,
    )
    return {"lambda": [result]}


# ═════════════════════════════════════════════════════════════════════════════
# LOGIQUE CENTRALE : DÉDUCTION DES DEFAULTS + APPLICATION
# ═════════════════════════════════════════════════════════════════════════════

def _build_defaults(detail: Dict, resource_id: str, resource_type: str) -> Dict[str, str]:
    """
    Déduit les valeurs de tags par défaut depuis le contexte de l'événement.
    Priorité : tags déjà fournis dans la requête > variables d'env > constantes.
    """
    # Tags fournis par l'appelant dans la requête (ex: RunInstances TagSpecification)
    request_tags = _extract_request_tags(detail, resource_type)

    # Identité de l'appelant (IAM user / role)
    caller = _extract_caller_identity(detail)

    return {
        "Owner":       request_tags.get("Owner", caller or DEFAULT_OWNER),
        "Squad":       request_tags.get("Squad", DEFAULT_SQUAD),
        "CostCenter":  request_tags.get("CostCenter", DEFAULT_COST_CENTER),
        "Environment": request_tags.get("Environment", DEFAULT_ENVIRONMENT),
        "ManagedBy":   "AutoTagger",
        "AutoTaggedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _compute_missing_tags(existing: Dict[str, str],
                          defaults: Dict[str, str]) -> Dict[str, str]:
    """Retourne uniquement les tags REQUIRED absents de existing."""
    return {
        tag: defaults[tag]
        for tag in REQUIRED_TAGS
        if tag not in existing and tag in defaults
    }


def _apply_tags(resource_id: str, resource_type: str,
                existing_tags: Dict[str, str],
                missing_tags_to_apply: Dict[str, str],
                tagger) -> Dict:
    """
    Applique les tags manquants (ou simule en DRY_RUN).
    Persiste le résultat dans DynamoDB.
    Retourne un dict de résultat pour le rapport.
    """
    result = {
        "resource_id":   resource_id,
        "resource_type": resource_type,
        "tags_added":    list(missing_tags_to_apply.keys()),
        "dry_run":       DRY_RUN,
        "status":        "skipped",
    }

    if not missing_tags_to_apply:
        result["status"] = "already_compliant"
        logger.info("[%s] %s — déjà conforme", resource_type, resource_id)
        _persist(resource_id, resource_type, already_compliant=True,
                 tags_added=[], dry_run=DRY_RUN)
        return result

    if DRY_RUN:
        result["status"] = "dry_run"
        logger.info("[DRY_RUN] [%s] %s — ajouterait : %s",
                    resource_type, resource_id, list(missing_tags_to_apply.keys()))
    else:
        try:
            tagger(missing_tags_to_apply)
            result["status"] = "tagged"
            logger.info("[%s] %s — tags appliqués : %s",
                        resource_type, resource_id, list(missing_tags_to_apply.keys()))
        except Exception as exc:
            result["status"] = "error"
            result["error"]  = str(exc)
            logger.error("[%s] %s — erreur tagging : %s",
                         resource_type, resource_id, exc)

    _persist(resource_id, resource_type,
             already_compliant=False,
             tags_added=list(missing_tags_to_apply.keys()),
             dry_run=DRY_RUN)
    return result


# ═════════════════════════════════════════════════════════════════════════════
# LECTURE DES TAGS EXISTANTS
# ═════════════════════════════════════════════════════════════════════════════

def _get_ec2_tags(instance_id: str) -> Dict[str, str]:
    try:
        resp = ec2_client.describe_instances(InstanceIds=[instance_id])
        raw  = resp["Reservations"][0]["Instances"][0].get("Tags", [])
        return {t["Key"]: t["Value"] for t in raw}
    except Exception:
        return {}


def _get_s3_tags(bucket_name: str) -> Dict[str, str]:
    try:
        resp = s3_client.get_bucket_tagging(Bucket=bucket_name)
        return {t["Key"]: t["Value"] for t in resp.get("TagSet", [])}
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchTagSet":
            return {}
        raise


def _get_rds_tags(db_arn: str) -> Dict[str, str]:
    try:
        resp = rds_client.list_tags_for_resource(ResourceName=db_arn)
        return {t["Key"]: t["Value"] for t in resp.get("TagList", [])}
    except Exception:
        return {}


def _get_lambda_tags(func_arn: str) -> Dict[str, str]:
    try:
        resp = lambda_client.list_tags(Resource=func_arn)
        return resp.get("Tags", {})
    except Exception:
        return {}


# ═════════════════════════════════════════════════════════════════════════════
# EXTRACTION DU CONTEXTE DE L'ÉVÉNEMENT
# ═════════════════════════════════════════════════════════════════════════════

def _extract_request_tags(detail: Dict, resource_type: str) -> Dict[str, str]:
    """Extrait les tags fournis dans la requête originale (avant création)."""
    tags: Dict[str, str] = {}

    if resource_type == "ec2":
        # TagSpecification dans RunInstances
        for spec in detail.get("requestParameters", {}).get("tagSpecificationSet", {}).get("items", []):
            for tag in spec.get("tags", {}).get("items", []):
                tags[tag.get("key", "")] = tag.get("value", "")

    elif resource_type == "s3":
        # Tagging dans CreateBucket (rare mais possible)
        for tag in detail.get("requestParameters", {}).get("Tagging", {}).get("TagSet", []):
            tags[tag.get("Key", "")] = tag.get("Value", "")

    elif resource_type == "rds":
        for tag in detail.get("requestParameters", {}).get("tags", []):
            tags[tag.get("key", "")] = tag.get("value", "")

    elif resource_type == "lambda":
        tags = detail.get("requestParameters", {}).get("tags", {})

    return tags


def _extract_caller_identity(detail: Dict) -> Optional[str]:
    """
    Tente de déduire un email Owner depuis l'identité IAM de l'appelant.
    Retourne None si non déductible.
    """
    identity = detail.get("userIdentity", {})
    arn      = identity.get("arn", "")

    # IAM user → arn:aws:iam::123:user/jean.dupont@entreprise.com
    if ":user/" in arn:
        username = arn.split(":user/")[-1]
        if "@" in username:
            return username

    # SSO / assumed-role → session name peut contenir l'email
    session = identity.get("sessionContext", {}).get("sessionIssuer", {})
    session_name = identity.get("principalId", "").split(":")[-1]
    if "@" in session_name:
        return session_name

    return None


# ═════════════════════════════════════════════════════════════════════════════
# PERSISTANCE DYNAMODB
# ═════════════════════════════════════════════════════════════════════════════

def _persist(resource_id: str, resource_type: str,
             already_compliant: bool, tags_added: List[str],
             dry_run: bool):
    """Écrit l'événement de tagging dans DynamoDB."""
    if not DYNAMODB_TABLE_NAME:
        return
    try:
        table      = dynamodb.Table(DYNAMODB_TABLE_NAME)
        now        = datetime.now(timezone.utc).isoformat()
        ttl_expiry = int(time.time()) + (90 * 24 * 3600)

        table.put_item(Item={
            "resource_id":       resource_id,
            "scan_timestamp":    now,
            "resource_type":     resource_type,
            "event":             "auto_tag",
            "already_compliant": already_compliant,
            "tags_added":        tags_added,
            "dry_run":           dry_run,
            "criticality":       "UNKNOWN",   # sera mis à jour par le scanner
            "environment":       DEFAULT_ENVIRONMENT,
            "ttl_expiry":        ttl_expiry,
        })
    except Exception as exc:
        logger.warning("DynamoDB write error (%s): %s", resource_id, exc)
