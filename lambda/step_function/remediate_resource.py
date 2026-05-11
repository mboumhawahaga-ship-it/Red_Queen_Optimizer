"""
🛑 REMEDIATE RESOURCE LAMBDA — Red Queen Step Function
Stop + quarantine une ressource non conforme. Ne supprime JAMAIS.

Actions par type :
  EC2    → stop_instances + tag Status=Quarantined
  RDS    → stop_db_instance + tag Status=Quarantined
  S3     → block_public_access + tag Status=Quarantined (pas de stop possible)
  Lambda → put_function_concurrency(0) + tag Status=Quarantined (throttle à 0)

DRY_RUN=true → simule sans toucher AWS.
Persiste l'état dans DynamoDB.
"""

import os
import json
import time
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DRY_RUN             = os.environ.get("DRY_RUN", "true").lower() == "true"
AWS_REGION          = os.environ.get("AWS_REGION", "eu-west-1")
DYNAMODB_TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME", "")

ec2_client = boto3.client("ec2")
rds_client = boto3.client("rds")
s3_client  = boto3.client("s3")
lam_client = boto3.client("lambda")
dynamodb   = boto3.resource("dynamodb")


def lambda_handler(event: Dict, context: Any) -> Dict:
    resource_id   = event.get("resource_id", "")
    resource_type = event.get("resource_type", "").upper()
    reason        = event.get("reason", "sla_expired")
    dry_run       = event.get("dry_run", DRY_RUN)
    if isinstance(dry_run, str):
        dry_run = dry_run.lower() == "true"

    logger.info(
        "Remediate — %s/%s reason=%s dry_run=%s",
        resource_type, resource_id, reason, dry_run
    )

    result = _remediate(resource_id, resource_type, reason, dry_run)
    _persist(resource_id, resource_type, result, dry_run)
    return result


# ── Remédiation par type ──────────────────────────────────────────────────────

def _remediate(resource_id: str, resource_type: str,
               reason: str, dry_run: bool) -> Dict:
    if dry_run:
        logger.info("[DRY_RUN] Remédiation simulée : %s/%s", resource_type, resource_id)
        return {
            "remediated": False,
            "dry_run":    True,
            "action":     "simulated",
            "resource_id":   resource_id,
            "resource_type": resource_type,
            "reason":        reason,
        }

    action = None
    error  = None

    try:
        if resource_type == "EC2":
            action = _remediate_ec2(resource_id)
        elif resource_type == "RDS":
            action = _remediate_rds(resource_id)
        elif resource_type == "S3":
            action = _remediate_s3(resource_id)
        elif resource_type == "LAMBDA":
            action = _remediate_lambda(resource_id)
        else:
            error = f"Type non supporté : {resource_type}"
    except Exception as exc:
        error = str(exc)
        logger.error("Erreur remédiation %s/%s : %s", resource_type, resource_id, exc)

    return {
        "remediated":    error is None,
        "dry_run":       False,
        "action":        action,
        "resource_id":   resource_id,
        "resource_type": resource_type,
        "reason":        reason,
        "error":         error,
    }


def _remediate_ec2(resource_id: str) -> str:
    """Stop l'instance + tag Quarantined."""
    ec2_client.stop_instances(InstanceIds=[resource_id])
    ec2_client.create_tags(
        Resources=[resource_id],
        Tags=[
            {"Key": "Status",          "Value": "Quarantined"},
            {"Key": "QuarantinedAt",   "Value": datetime.now(timezone.utc).isoformat()},
            {"Key": "QuarantineReason","Value": "missing_required_tags"},
        ]
    )
    logger.info("EC2 %s stoppée et mise en quarantaine", resource_id)
    return "stopped_and_quarantined"


def _remediate_rds(resource_id: str) -> str:
    """Stop l'instance RDS + tag Quarantined."""
    try:
        rds_client.stop_db_instance(DBInstanceIdentifier=resource_id)
    except ClientError as e:
        # RDS Multi-AZ ne supporte pas stop — on tag quand même
        if "InvalidDBInstanceState" in str(e):
            logger.warning("RDS %s ne peut pas être stoppée (Multi-AZ?) — tag seul", resource_id)
        else:
            raise

    # Récupérer l'ARN pour tagger
    resp   = rds_client.describe_db_instances(DBInstanceIdentifier=resource_id)
    db_arn = resp["DBInstances"][0]["DBInstanceArn"]
    rds_client.add_tags_to_resource(
        ResourceName=db_arn,
        Tags=[
            {"Key": "Status",          "Value": "Quarantined"},
            {"Key": "QuarantinedAt",   "Value": datetime.now(timezone.utc).isoformat()},
            {"Key": "QuarantineReason","Value": "missing_required_tags"},
        ]
    )
    logger.info("RDS %s stoppée et mise en quarantaine", resource_id)
    return "stopped_and_quarantined"


def _remediate_s3(resource_id: str) -> str:
    """Bloque l'accès public + tag Quarantined."""
    s3_client.put_public_access_block(
        Bucket=resource_id,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls":       True,
            "IgnorePublicAcls":      True,
            "BlockPublicPolicy":     True,
            "RestrictPublicBuckets": True,
        }
    )
    # Fusionner avec les tags existants
    try:
        existing = s3_client.get_bucket_tagging(Bucket=resource_id).get("TagSet", [])
    except ClientError:
        existing = []
    merged = {t["Key"]: t["Value"] for t in existing}
    merged.update({
        "Status":           "Quarantined",
        "QuarantinedAt":    datetime.now(timezone.utc).isoformat(),
        "QuarantineReason": "missing_required_tags",
    })
    s3_client.put_bucket_tagging(
        Bucket=resource_id,
        Tagging={"TagSet": [{"Key": k, "Value": v} for k, v in merged.items()]}
    )
    logger.info("S3 %s accès public bloqué et mis en quarantaine", resource_id)
    return "public_access_blocked_and_quarantined"


def _remediate_lambda(resource_id: str) -> str:
    """Throttle la Lambda à 0 concurrence + tag Quarantined."""
    lam_client.put_function_concurrency(
        FunctionName=resource_id,
        ReservedConcurrentExecutions=0
    )
    func_arn = lam_client.get_function(
        FunctionName=resource_id
    )["Configuration"]["FunctionArn"]
    lam_client.tag_resource(
        Resource=func_arn,
        Tags={
            "Status":           "Quarantined",
            "QuarantinedAt":    datetime.now(timezone.utc).isoformat(),
            "QuarantineReason": "missing_required_tags",
        }
    )
    logger.info("Lambda %s throttlée à 0 et mise en quarantaine", resource_id)
    return "throttled_and_quarantined"


# ── DynamoDB ──────────────────────────────────────────────────────────────────

def _persist(resource_id: str, resource_type: str, result: Dict, dry_run: bool):
    if not DYNAMODB_TABLE_NAME:
        return
    try:
        table      = dynamodb.Table(DYNAMODB_TABLE_NAME)
        now        = datetime.now(timezone.utc).isoformat()
        ttl_expiry = int(time.time()) + (90 * 24 * 3600)

        table.put_item(Item={
            "resource_id":    resource_id,
            "scan_timestamp": now,
            "resource_type":  resource_type,
            "event":          "remediation",
            "action":         result.get("action", "unknown"),
            "reason":         result.get("reason", "unknown"),
            "remediated":     result.get("remediated", False),
            "dry_run":        dry_run,
            "criticality":    "UNKNOWN",
            "environment":    "unknown",
            "compliant":      False,
            "ttl_expiry":     ttl_expiry,
        })
    except Exception as exc:
        logger.warning("DynamoDB write error (%s): %s", resource_id, exc)
