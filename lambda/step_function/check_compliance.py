"""
🔍 CHECK COMPLIANCE LAMBDA — Red Queen Step Function
Invoquée par la Step Function après la période de grâce SLOW (72h).
Lit DynamoDB pour déterminer si la ressource est devenue conforme.

Retourne : { "compliant": bool, "feedback_action": str | None }
"""

import os
import logging
from typing import Dict, Any, Optional

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DYNAMODB_TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME", "")
dynamodb = boto3.resource("dynamodb")


def lambda_handler(event: Dict, context: Any) -> Dict:
    resource_id   = event.get("resource_id", "")
    resource_type = event.get("resource_type", "")

    logger.info("CheckCompliance — %s/%s", resource_type, resource_id)

    record = _get_latest_record(resource_id)

    if not record:
        logger.warning("Aucun enregistrement DynamoDB pour %s", resource_id)
        return {"compliant": False, "feedback_action": None, "source": "not_found"}

    # Conforme si : le scanner a marqué compliant=True OU le propriétaire a taggé/approuvé
    feedback_action = record.get("feedback_action")
    compliant       = bool(record.get("compliant", False))

    if feedback_action in ("approve", "tag"):
        compliant = True

    logger.info(
        "Résultat — compliant=%s feedback_action=%s",
        compliant, feedback_action
    )

    return {
        "compliant":       compliant,
        "feedback_action": feedback_action,
        "resource_id":     resource_id,
        "resource_type":   resource_type,
        "source":          "dynamodb",
    }


def _get_latest_record(resource_id: str) -> Optional[Dict]:
    """Récupère l'entrée DynamoDB la plus récente pour cette ressource."""
    if not DYNAMODB_TABLE_NAME:
        return None
    try:
        table = dynamodb.Table(DYNAMODB_TABLE_NAME)
        resp  = table.query(
            KeyConditionExpression="resource_id = :rid",
            ExpressionAttributeValues={":rid": resource_id},
            ScanIndexForward=False,
            Limit=1,
        )
        items = resp.get("Items", [])
        return items[0] if items else None
    except Exception as exc:
        logger.error("DynamoDB query error (%s): %s", resource_id, exc)
        return None
