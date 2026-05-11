"""
🔗 FEEDBACK LAMBDA — Red Queen Governance
Reçoit les actions propriétaires via les liens inclus dans les emails SNS.

Endpoints (Lambda URL) :
  GET /?resource_id=i-xxx&action=approve&token=abc  → approuve la ressource
  GET /?resource_id=i-xxx&action=reject&token=abc   → rejette / demande grâce
  GET /?resource_id=i-xxx&action=tag&token=abc&owner=…&squad=…  → applique les tags

Sécurité :
  - Token HMAC-SHA256 signé avec FEEDBACK_SECRET (vérifié à chaque requête)
  - Idempotence : une action déjà traitée retourne une page d'info sans re-exécuter
  - DRY_RUN=true : simule sans écrire sur AWS ni DynamoDB

Step Function :
  - Après chaque action, récupère le taskToken stocké par notify.py dans DynamoDB
  - Appelle SendTaskSuccess (approve/tag) ou SendTaskSuccess avec feedback_action=reject
    pour reprendre l'exécution de la Step Function
"""

import os
import json
import hmac
import hashlib
import time
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ── Configuration ─────────────────────────────────────────────────────────────
DYNAMODB_TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME", "")
FEEDBACK_SECRET     = os.environ.get("FEEDBACK_SECRET", "")
DRY_RUN             = os.environ.get("DRY_RUN", "true").lower() == "true"
AWS_REGION          = os.environ.get("AWS_REGION", "eu-west-1")

VALID_ACTIONS = {"approve", "reject", "tag"}

# ── Clients AWS ───────────────────────────────────────────────────────────────
dynamodb   = boto3.resource("dynamodb")
ec2_client = boto3.client("ec2")
rds_client = boto3.client("rds")
s3_client  = boto3.client("s3")
lam_client = boto3.client("lambda")
sfn_client = boto3.client("stepfunctions")


# ═════════════════════════════════════════════════════════════════════════════
# POINT D'ENTRÉE — Lambda URL
# ═════════════════════════════════════════════════════════════════════════════

def lambda_handler(event: Dict, context: Any) -> Dict:
    logger.info("Feedback reçu — DRY_RUN=%s", DRY_RUN)

    params        = _parse_query(event)
    resource_id   = params.get("resource_id", "")
    resource_type = params.get("resource_type", "")
    action        = params.get("action", "")
    token         = params.get("token", "")

    # ── Validation ────────────────────────────────────────────────────────────
    if not resource_id or action not in VALID_ACTIONS:
        return _html_response(400, _page_error(
            "Lien invalide", "Paramètres manquants ou action inconnue."
        ))

    if not _verify_token(token, resource_id, action):
        return _html_response(403, _page_error(
            "Lien expiré ou invalide",
            "Ce lien de feedback n'est plus valide. "
            "Contactez votre équipe de gouvernance."
        ))

    # ── Idempotence ───────────────────────────────────────────────────────────
    existing = _get_latest_record(resource_id, event_filter="feedback")
    if existing and existing.get("feedback_action"):
        return _html_response(200, _page_already_done(
            resource_id, existing["feedback_action"]
        ))

    # ── Dispatch ──────────────────────────────────────────────────────────────
    if action == "approve":
        response = _handle_approve(resource_id, resource_type, params)
    elif action == "reject":
        response = _handle_reject(resource_id, resource_type, params)
    else:  # tag
        response = _handle_tag(resource_id, resource_type, params)
        if response["statusCode"] != 200:
            return response

    # ── Reprendre la Step Function si un taskToken est en attente ─────────────
    _resume_step_function(resource_id, action)

    return response


# ═════════════════════════════════════════════════════════════════════════════
# ACTIONS
# ═════════════════════════════════════════════════════════════════════════════

def _handle_approve(resource_id: str, resource_type: str, params: Dict) -> Dict:
    logger.info("APPROVE — %s %s", resource_type, resource_id)
    _persist_feedback(resource_id, "approve", params)
    return _html_response(200, _page_success(
        "✅ Ressource approuvée", resource_id,
        "Votre ressource a été marquée comme légitime. "
        "Aucune action de suppression ne sera effectuée.",
        color="#27ae60"
    ))


def _handle_reject(resource_id: str, resource_type: str, params: Dict) -> Dict:
    logger.info("REJECT — %s %s", resource_type, resource_id)
    _persist_feedback(resource_id, "reject", params)
    return _html_response(200, _page_success(
        "🗑️ Ressource marquée pour suppression", resource_id,
        "La ressource sera supprimée lors du prochain cycle de cleanup.",
        color="#e74c3c"
    ))


def _handle_tag(resource_id: str, resource_type: str, params: Dict) -> Dict:
    owner       = params.get("owner", "")
    squad       = params.get("squad", "")
    cost_center = params.get("cost_center", "")
    environment = params.get("environment", "dev")

    missing = [k for k, v in [
        ("owner", owner), ("squad", squad), ("cost_center", cost_center)
    ] if not v]

    if missing:
        return _html_response(400, _page_error(
            "Tags incomplets",
            f"Paramètres manquants : {', '.join(missing)}. "
            "Utilisez le lien complet fourni dans l'email."
        ))

    tags_to_apply = {
        "Owner": owner, "Squad": squad,
        "CostCenter": cost_center, "Environment": environment,
        "TaggedVia": "FeedbackLink",
    }

    error = None
    if not DRY_RUN:
        error = _apply_tags_to_resource(resource_id, resource_type, tags_to_apply)

    if error:
        logger.error("Erreur application tags %s : %s", resource_id, error)
        return _html_response(500, _page_error(
            "Erreur lors du tagging",
            f"Impossible d'appliquer les tags : {error}"
        ))

    _persist_feedback(resource_id, "tag", params, tags_applied=tags_to_apply)

    mode_note = " (simulation DRY_RUN)" if DRY_RUN else ""
    return _html_response(200, _page_success(
        "🏷️ Tags appliqués" + mode_note, resource_id,
        f"Tags ajoutés : Owner={owner}, Squad={squad}, "
        f"CostCenter={cost_center}, Environment={environment}",
        color="#2980b9"
    ))


# ═════════════════════════════════════════════════════════════════════════════
# STEP FUNCTION — reprise via taskToken
# ═════════════════════════════════════════════════════════════════════════════

def _resume_step_function(resource_id: str, action: str):
    """
    Récupère le taskToken stocké par notify.py et appelle SendTaskSuccess
    pour reprendre l'exécution de la Step Function en attente.
    """
    token_record = _get_latest_record(resource_id, event_filter="sfn_wait")
    if not token_record:
        logger.info("Pas de taskToken en attente pour %s", resource_id)
        return

    task_token = token_record.get("task_token")
    if not task_token:
        return

    output = json.dumps({
        "feedback_action": action,
        "resource_id":     resource_id,
    })

    try:
        sfn_client.send_task_success(taskToken=task_token, output=output)
        logger.info("SendTaskSuccess envoyé pour %s (action=%s)", resource_id, action)
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("TaskDoesNotExist", "TaskTimedOut", "InvalidToken"):
            logger.warning("taskToken invalide ou expiré (%s) — ignoré", code)
        else:
            logger.error("Erreur SendTaskSuccess : %s", exc)


# ═════════════════════════════════════════════════════════════════════════════
# APPLICATION DES TAGS
# ═════════════════════════════════════════════════════════════════════════════

def _apply_tags_to_resource(resource_id: str, resource_type: str,
                             tags: Dict[str, str]) -> Optional[str]:
    tag_list = [{"Key": k, "Value": v} for k, v in tags.items()]
    try:
        rt = resource_type.upper()
        if rt == "EC2":
            ec2_client.create_tags(Resources=[resource_id], Tags=tag_list)
        elif rt == "S3":
            try:
                existing_resp = s3_client.get_bucket_tagging(Bucket=resource_id)
                existing_tags = existing_resp.get("TagSet", [])
            except ClientError:
                existing_tags = []
            merged = {t["Key"]: t["Value"] for t in existing_tags}
            merged.update(tags)
            s3_client.put_bucket_tagging(
                Bucket=resource_id,
                Tagging={"TagSet": [{"Key": k, "Value": v} for k, v in merged.items()]}
            )
        elif rt == "RDS":
            account_id = boto3.client("sts").get_caller_identity()["Account"]
            db_arn = f"arn:aws:rds:{AWS_REGION}:{account_id}:db:{resource_id}"
            rds_client.add_tags_to_resource(ResourceName=db_arn, Tags=tag_list)
        elif rt == "LAMBDA":
            func     = lam_client.get_function(FunctionName=resource_id)
            func_arn = func["Configuration"]["FunctionArn"]
            lam_client.tag_resource(Resource=func_arn, Tags=tags)
        else:
            return f"Type de ressource non supporté : {resource_type}"
        return None
    except Exception as exc:
        return str(exc)


# ═════════════════════════════════════════════════════════════════════════════
# DYNAMODB
# ═════════════════════════════════════════════════════════════════════════════

def _get_latest_record(resource_id: str,
                       event_filter: Optional[str] = None) -> Optional[Dict]:
    """Récupère l'entrée DynamoDB la plus récente, filtrée par event si fourni."""
    if not DYNAMODB_TABLE_NAME:
        return None
    try:
        table = dynamodb.Table(DYNAMODB_TABLE_NAME)
        # Récupère les N derniers et filtre côté client (évite un GSI supplémentaire)
        resp  = table.query(
            KeyConditionExpression="resource_id = :rid",
            ExpressionAttributeValues={":rid": resource_id},
            ScanIndexForward=False,
            Limit=20,
        )
        items = resp.get("Items", [])
        if event_filter:
            items = [i for i in items if i.get("event") == event_filter]
        return items[0] if items else None
    except Exception as exc:
        logger.warning("DynamoDB get error (%s): %s", resource_id, exc)
        return None


def _persist_feedback(resource_id: str, action: str,
                      params: Dict, tags_applied: Dict = None):
    if not DYNAMODB_TABLE_NAME:
        return
    try:
        table      = dynamodb.Table(DYNAMODB_TABLE_NAME)
        now        = datetime.now(timezone.utc).isoformat()
        ttl_expiry = int(time.time()) + (90 * 24 * 3600)

        item = {
            "resource_id":     resource_id,
            "scan_timestamp":  now,
            "resource_type":   params.get("resource_type", "UNKNOWN"),
            "event":           "feedback",
            "feedback_action": action,
            "dry_run":         DRY_RUN,
            "environment":     params.get("environment", "unknown"),
            "criticality":     params.get("criticality", "UNKNOWN"),
            "compliant":       action in ("approve", "tag"),
            "ttl_expiry":      ttl_expiry,
        }
        if tags_applied:
            item["tags_applied"] = tags_applied
        table.put_item(Item=item)
    except Exception as exc:
        logger.warning("DynamoDB write error (%s): %s", resource_id, exc)


# ═════════════════════════════════════════════════════════════════════════════
# SÉCURITÉ — TOKEN HMAC
# ═════════════════════════════════════════════════════════════════════════════

def generate_token(resource_id: str, action: str) -> str:
    if not FEEDBACK_SECRET:
        return "no-secret-configured"
    payload = f"{resource_id}:{action}"
    return hmac.new(
        FEEDBACK_SECRET.encode(), payload.encode(), hashlib.sha256
    ).hexdigest()


def _verify_token(token: str, resource_id: str, action: str) -> bool:
    if not FEEDBACK_SECRET:
        logger.warning("FEEDBACK_SECRET non configuré — token non vérifié")
        return True
    return hmac.compare_digest(token, generate_token(resource_id, action))


# ═════════════════════════════════════════════════════════════════════════════
# PARSING
# ═════════════════════════════════════════════════════════════════════════════

def _parse_query(event: Dict) -> Dict[str, str]:
    raw = event.get("queryStringParameters") or {}
    return {k: v for k, v in raw.items()}


# ═════════════════════════════════════════════════════════════════════════════
# RÉPONSES HTML
# ═════════════════════════════════════════════════════════════════════════════

def _html_response(status: int, body: str) -> Dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "text/html; charset=utf-8"},
        "body": body,
    }


def _page_success(title: str, resource_id: str, message: str, color: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: #f5f6fa; display: flex; justify-content: center;
           align-items: center; min-height: 100vh; margin: 0; }}
    .card {{ background: white; border-radius: 12px; padding: 40px 48px;
             box-shadow: 0 4px 24px rgba(0,0,0,.08); max-width: 480px;
             text-align: center; }}
    .badge {{ display: inline-block; background: {color}; color: white;
              border-radius: 6px; padding: 4px 12px; font-size: 13px;
              font-weight: 600; margin-bottom: 16px; }}
    h1 {{ font-size: 22px; color: #2c3e50; margin: 0 0 12px; }}
    p  {{ color: #636e72; line-height: 1.6; margin: 0 0 8px; }}
    .rid {{ font-family: monospace; background: #f0f0f0; padding: 2px 8px;
            border-radius: 4px; font-size: 13px; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="badge">{title}</div>
    <h1>Action enregistrée</h1>
    <p>Ressource : <span class="rid">{resource_id}</span></p>
    <p>{message}</p>
  </div>
</body>
</html>"""


def _page_error(title: str, message: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <title>Erreur — {title}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           background: #f5f6fa; display: flex; justify-content: center;
           align-items: center; min-height: 100vh; margin: 0; }}
    .card {{ background: white; border-radius: 12px; padding: 40px 48px;
             box-shadow: 0 4px 24px rgba(0,0,0,.08); max-width: 480px;
             text-align: center; }}
    h1 {{ font-size: 20px; color: #e74c3c; margin: 0 0 12px; }}
    p  {{ color: #636e72; line-height: 1.6; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>⚠️ {title}</h1>
    <p>{message}</p>
  </div>
</body>
</html>"""


def _page_already_done(resource_id: str, previous_action: str) -> str:
    labels = {
        "approve": "déjà approuvée ✅",
        "reject":  "déjà marquée pour suppression 🗑️",
        "tag":     "déjà retaggée 🏷️",
    }
    label = labels.get(previous_action, f"déjà traitée ({previous_action})")
    return _page_success(
        "Action déjà effectuée", resource_id,
        f"Cette ressource a été {label}. Aucune action supplémentaire requise.",
        color="#7f8c8d"
    )
