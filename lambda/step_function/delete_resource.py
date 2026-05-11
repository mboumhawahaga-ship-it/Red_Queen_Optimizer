"""
🗑️  DELETE RESOURCE LAMBDA — Red Queen Step Function
Invoquée par la Step Function pour supprimer une ressource non conforme.

Deux modes d'invocation :
  1. Avec task_token (waitForTaskToken) → supprime puis appelle SendTaskSuccess
  2. Sans task_token (timeout path)     → supprime directement

Supporte DRY_RUN=true pour simulation sans suppression réelle.
"""

import os
import json
import logging
from typing import Dict, Any, Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DRY_RUN    = os.environ.get("DRY_RUN", "true").lower() == "true"
AWS_REGION = os.environ.get("AWS_REGION", "eu-west-1")

ec2_client = boto3.client("ec2")
rds_client = boto3.client("rds")
s3_client  = boto3.client("s3")
lam_client = boto3.client("lambda")
sfn_client = boto3.client("stepfunctions")
sts_client = boto3.client("sts")


def lambda_handler(event: Dict, context: Any) -> Dict:
    resource_id   = event.get("resource_id", "")
    resource_type = event.get("resource_type", "").upper()
    reason        = event.get("reason", "unknown")
    task_token    = event.get("task_token")
    dry_run       = event.get("dry_run", DRY_RUN)
    if isinstance(dry_run, str):
        dry_run = dry_run.lower() == "true"

    logger.info(
        "DeleteResource — %s/%s reason=%s dry_run=%s",
        resource_type, resource_id, reason, dry_run
    )

    result = _delete(resource_id, resource_type, dry_run)

    # Reprendre la Step Function si on a un taskToken
    if task_token:
        _resume_step_function(task_token, result)

    return result


# ── Suppression par type ──────────────────────────────────────────────────────

def _delete(resource_id: str, resource_type: str, dry_run: bool) -> Dict:
    if dry_run:
        logger.info("[DRY_RUN] Suppression simulée : %s/%s", resource_type, resource_id)
        return {
            "deleted": False,
            "dry_run": True,
            "resource_id": resource_id,
            "resource_type": resource_type,
        }

    error = None
    try:
        if resource_type == "EC2":
            ec2_client.terminate_instances(InstanceIds=[resource_id])

        elif resource_type == "RDS":
            rds_client.delete_db_instance(
                DBInstanceIdentifier=resource_id,
                SkipFinalSnapshot=True,
            )

        elif resource_type == "S3":
            _empty_bucket(resource_id)
            s3_client.delete_bucket(Bucket=resource_id)

        elif resource_type == "LAMBDA":
            lam_client.delete_function(FunctionName=resource_id)

        else:
            error = f"Type non supporté : {resource_type}"

    except Exception as exc:
        error = str(exc)
        logger.error("Erreur suppression %s/%s : %s", resource_type, resource_id, exc)

    return {
        "deleted":       error is None,
        "dry_run":       False,
        "resource_id":   resource_id,
        "resource_type": resource_type,
        "error":         error,
    }


def _empty_bucket(bucket_name: str):
    """Vide un bucket S3 avant suppression."""
    paginator = s3_client.get_paginator("list_object_versions")
    for page in paginator.paginate(Bucket=bucket_name):
        objs = [
            {"Key": v["Key"], "VersionId": v["VersionId"]}
            for v in page.get("Versions", []) + page.get("DeleteMarkers", [])
        ]
        if objs:
            s3_client.delete_objects(Bucket=bucket_name, Delete={"Objects": objs})


# ── Reprise Step Function ─────────────────────────────────────────────────────

def _resume_step_function(task_token: str, result: Dict):
    """Appelle SendTaskSuccess pour reprendre l'exécution de la Step Function."""
    try:
        sfn_client.send_task_success(
            taskToken=task_token,
            output=json.dumps(result),
        )
        logger.info("SendTaskSuccess envoyé")
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("TaskDoesNotExist", "TaskTimedOut", "InvalidToken"):
            logger.warning("taskToken invalide ou expiré (%s) — ignoré", code)
        else:
            raise
