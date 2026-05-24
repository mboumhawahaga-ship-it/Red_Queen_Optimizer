"""
AUTO-TAGGER — Red Queen Governance (Phase 2 migration)

Listens to CloudTrail events via EventBridge and automatically applies
missing required tags on newly created resources.

Supported events:
  EC2    → RunInstances
  S3     → CreateBucket
  RDS    → CreateDBInstance
  Lambda → CreateFunction20150331

Behaviour:
  DRY_RUN=true  : logs what would be tagged, writes nothing to AWS
  DRY_RUN=false : applies missing tags + persists to DynamoDB
"""

import os
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

import boto3
from botocore.exceptions import ClientError

from ..shared.constants import WATCHED_EVENTS
from ..shared.utils import (
    check_required_tags,
    tags_list_to_dict,
    put_governance_record,
    sanitize_log_value,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Configuration ─────────────────────────────────────────────────────────────

DRY_RUN             = os.environ.get("DRY_RUN", "true").lower() == "true"
GOVERNANCE_TABLE    = os.environ.get("GOVERNANCE_TABLE", "")
DEFAULT_ENVIRONMENT = os.environ.get("DEFAULT_ENVIRONMENT", "dev")
DEFAULT_SQUAD       = os.environ.get("DEFAULT_SQUAD", "unknown")
DEFAULT_COST_CENTER = os.environ.get("DEFAULT_COST_CENTER", "CC-000")
DEFAULT_OWNER       = os.environ.get("DEFAULT_OWNER", "auto-tagger@entreprise.com")

# ── AWS clients ───────────────────────────────────────────────────────────────

ec2_client    = boto3.client("ec2")
s3_client     = boto3.client("s3")
rds_client    = boto3.client("rds")
lambda_client = boto3.client("lambda")


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def lambda_handler(event: Dict, context: Any) -> Dict:
    logger.info("Auto-tagger started — DRY_RUN=%s", DRY_RUN)
    logger.debug("Event received: %s", json.dumps(event, default=str))

    detail     = event.get("detail", {})
    event_name = detail.get("eventName", "")
    service    = WATCHED_EVENTS.get(event_name)

    if not service:
        logger.info("Event ignored: %s", sanitize_log_value(event_name))
        return {"statusCode": 200, "body": json.dumps({"skipped": event_name})}

    result = _dispatch(service, event_name, detail)
    return {"statusCode": 200, "body": json.dumps(result, default=str)}


# ══════════════════════════════════════════════════════════════════════════════
# DISPATCH BY SERVICE
# ══════════════════════════════════════════════════════════════════════════════

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

    db_arn = _rds_arn(db_id)
    if not db_arn:
        logger.warning("RDS ARN not found for %s — skipping", sanitize_log_value(db_id))
        return {"rds": []}

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

    func_arn = (detail.get("responseElements", {})
                      .get("functionArn", ""))
    existing = _get_lambda_tags(func_arn) if func_arn else {}
    defaults = _build_defaults(detail, func_name, "lambda")
    missing  = _compute_missing_tags(existing, defaults)
    result   = _apply_tags(
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


# ══════════════════════════════════════════════════════════════════════════════
# CORE LOGIC: DEFAULT TAGS + APPLICATION
# ══════════════════════════════════════════════════════════════════════════════

def _build_defaults(detail: Dict, resource_id: str, resource_type: str) -> Dict[str, str]:
    """
    Derives default tag values from the event context.
    Priority: tags already provided in the request > env vars > constants.
    """
    request_tags = _extract_request_tags(detail, resource_type)
    caller       = _extract_caller_identity(detail)

    return {
        "Owner":        request_tags.get("Owner", caller or DEFAULT_OWNER),
        "Squad":        request_tags.get("Squad", DEFAULT_SQUAD),
        "CostCenter":   request_tags.get("CostCenter", DEFAULT_COST_CENTER),
        "Environment":  request_tags.get("Environment", DEFAULT_ENVIRONMENT),
        "ManagedBy":    "AutoTagger",
        "AutoTaggedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _compute_missing_tags(existing: Dict[str, str],
                          defaults: Dict[str, str]) -> Dict[str, str]:
    """Returns only the REQUIRED tags absent from existing, with their default values."""
    existing_list = [{"Key": k, "Value": v} for k, v in existing.items()]
    _, missing_names = check_required_tags(existing_list)
    return {tag: defaults[tag] for tag in missing_names if tag in defaults}


def _apply_tags(resource_id: str, resource_type: str,
                existing_tags: Dict[str, str],
                missing_tags_to_apply: Dict[str, str],
                tagger) -> Dict:
    """
    Applies missing tags (or simulates in DRY_RUN).
    Persists the result to DynamoDB via put_governance_record.
    Returns a result dict for the report.
    """
    safe_id   = sanitize_log_value(resource_id)
    safe_type = sanitize_log_value(resource_type)

    result = {
        "resource_id":   resource_id,
        "resource_type": resource_type,
        "tags_added":    list(missing_tags_to_apply.keys()),
        "dry_run":       DRY_RUN,
        "status":        "skipped",
    }

    if not missing_tags_to_apply:
        result["status"] = "already_compliant"
        logger.info("[%s] %s — already compliant", safe_type, safe_id)
        put_governance_record(
            GOVERNANCE_TABLE, resource_id, resource_type, "auto_tag",
            compliant=True,
            environment=DEFAULT_ENVIRONMENT,
            dry_run=DRY_RUN,
            extra={"already_compliant": True, "tags_added": []},
        )
        return result

    if DRY_RUN:
        result["status"] = "dry_run"
        logger.info("[DRY_RUN] [%s] %s — would add: %s",
                    safe_type, safe_id, list(missing_tags_to_apply.keys()))
    else:
        try:
            tagger(missing_tags_to_apply)
            result["status"] = "tagged"
            logger.info("[%s] %s — tags applied: %s",
                        safe_type, safe_id, list(missing_tags_to_apply.keys()))
        except Exception as exc:
            result["status"] = "error"
            result["error"]  = str(exc)
            logger.error("[%s] %s — tagging error: %s",
                         safe_type, safe_id, exc)

    tags_added = list(missing_tags_to_apply.keys())
    put_governance_record(
        GOVERNANCE_TABLE, resource_id, resource_type, "auto_tag",
        compliant=False,
        missing_tags=tags_added,
        environment=DEFAULT_ENVIRONMENT,
        dry_run=DRY_RUN,
        extra={"already_compliant": False, "tags_added": tags_added},
    )
    return result


# ══════════════════════════════════════════════════════════════════════════════
# READ EXISTING TAGS
# ══════════════════════════════════════════════════════════════════════════════

def _get_ec2_tags(instance_id: str) -> Dict[str, str]:
    try:
        resp = ec2_client.describe_instances(InstanceIds=[instance_id])
        raw  = resp["Reservations"][0]["Instances"][0].get("Tags", [])
        return tags_list_to_dict(raw)
    except Exception:
        return {}


def _get_s3_tags(bucket_name: str) -> Dict[str, str]:
    try:
        resp = s3_client.get_bucket_tagging(Bucket=bucket_name)
        return tags_list_to_dict(resp.get("TagSet", []))
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchTagSet":
            return {}
        raise


def _rds_arn(db_identifier: str) -> Optional[str]:
    """Resolve a DB instance identifier to its canonical ARN via describe_db_instances."""
    try:
        resp = rds_client.describe_db_instances(DBInstanceIdentifier=db_identifier)
        instances = resp.get("DBInstances", [])
        return instances[0].get("DBInstanceArn") if instances else None
    except ClientError as exc:
        logger.error("RDS describe error (%s): %s", sanitize_log_value(db_identifier), exc)
        return None


def _get_rds_tags(db_arn: str) -> Dict[str, str]:
    try:
        resp = rds_client.list_tags_for_resource(ResourceName=db_arn)
        return tags_list_to_dict(resp.get("TagList", []))
    except Exception:
        return {}


def _get_lambda_tags(func_arn: str) -> Dict[str, str]:
    try:
        resp = lambda_client.list_tags(Resource=func_arn)
        return resp.get("Tags", {})
    except Exception:
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# EVENT CONTEXT EXTRACTION
# ══════════════════════════════════════════════════════════════════════════════

def _extract_request_tags(detail: Dict, resource_type: str) -> Dict[str, str]:
    """Extracts tags provided in the original API request (before resource creation)."""
    tags: Dict[str, str] = {}

    if resource_type == "ec2":
        for spec in (detail.get("requestParameters", {})
                           .get("tagSpecificationSet", {})
                           .get("items", [])):
            for tag in spec.get("tags", {}).get("items", []):
                tags[tag.get("key", "")] = tag.get("value", "")

    elif resource_type == "s3":
        for tag in (detail.get("requestParameters", {})
                          .get("Tagging", {})
                          .get("TagSet", [])):
            tags[tag.get("Key", "")] = tag.get("Value", "")

    elif resource_type == "rds":
        for tag in detail.get("requestParameters", {}).get("tags", []):
            tags[tag.get("key", "")] = tag.get("value", "")

    elif resource_type == "lambda":
        tags = detail.get("requestParameters", {}).get("tags", {})

    return tags


def _extract_caller_identity(detail: Dict) -> Optional[str]:
    """
    Attempts to derive an Owner email from the IAM caller identity.
    Returns None if not derivable.
    """
    identity = detail.get("userIdentity", {})
    arn      = identity.get("arn", "")

    if ":user/" in arn:
        username = arn.split(":user/")[-1]
        if "@" in username:
            return username

    session_name = identity.get("principalId", "").split(":")[-1]
    if "@" in session_name:
        return session_name

    return None
