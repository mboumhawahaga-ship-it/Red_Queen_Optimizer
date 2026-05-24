"""
Feedback API — Red Queen Governance (Phase 3)

API Gateway Lambda proxy integration.

Routes
------
GET  /feedback?resource_id={id}   → latest governance record for a resource
POST /feedback                    → submit an owner feedback action

Actions (defined in shared/constants VALID_ACTIONS):
  approve  → status=handled   resource is intentionally exempt from tagging policy
  tag      → status=resolved  owner claims required tags have been added
  reject   → status=alerted   owner disputes the finding; re-enters normal SLA flow

No HMAC tokens, no Step Functions task tokens.
Authentication is handled at the API Gateway layer (IAM / Cognito).
"""

import os
import json
import decimal
import logging
from typing import Any, Dict, Optional

from ..shared.constants import VALID_ACTIONS
from ..shared.utils import (
    sanitize_resource_id,
    sanitize_log_value,
    get_governance_record,
    put_governance_record,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ── Env vars ──────────────────────────────────────────────────────────────────

TABLE_NAME = os.environ.get("GOVERNANCE_TABLE", "")
DRY_RUN    = os.environ.get("DRY_RUN", "true").lower() == "true"

# ── Action → governance status mapping ───────────────────────────────────────

_ACTION_STATUS: Dict[str, str] = {
    "approve": "handled",   # resource intentionally exempt — excluded from future evals
    "tag":     "resolved",  # owner asserts tags added — Config will verify on next eval
    "reject":  "alerted",   # owner disputes — record rejection and keep in SLA flow
}


# ── Entry point ───────────────────────────────────────────────────────────────

def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    method = event.get("httpMethod", "")

    if method == "GET":
        return _handle_get(event)
    if method == "POST":
        return _handle_post(event)

    return _response(405, {"error": "method_not_allowed", "allowed": ["GET", "POST"]})


# ── GET /feedback?resource_id={id} ───────────────────────────────────────────

def _handle_get(event: Dict[str, Any]) -> Dict[str, Any]:
    params      = event.get("queryStringParameters") or {}
    resource_id = params.get("resource_id", "").strip()

    if not resource_id:
        return _response(400, {"error": "missing_resource_id"})

    try:
        resource_id = sanitize_resource_id(resource_id)
    except ValueError:
        return _response(400, {"error": "invalid_resource_id"})

    record = get_governance_record(TABLE_NAME, resource_id)
    if record is None:
        return _response(404, {"error": "not_found", "resource_id": resource_id})

    logger.info("GET feedback resource=%s status=%s",
                sanitize_log_value(resource_id),
                sanitize_log_value(str(record.get("status", "unknown"))))
    return _response(200, record)


# ── POST /feedback ────────────────────────────────────────────────────────────

def _handle_post(event: Dict[str, Any]) -> Dict[str, Any]:
    body = _parse_body(event)
    if body is None:
        return _response(400, {"error": "invalid_json"})

    resource_id = str(body.get("resource_id", "")).strip()
    action      = str(body.get("action", "")).strip()
    comment     = str(body.get("comment", "")).strip()

    # ── Validate inputs ───────────────────────────────────────────────────────
    if not resource_id:
        return _response(400, {"error": "missing_resource_id"})
    if not action:
        return _response(400, {"error": "missing_action"})
    if action not in VALID_ACTIONS:
        return _response(400, {
            "error":    "invalid_action",
            "received": action,
            "valid":    sorted(VALID_ACTIONS),
        })

    try:
        resource_id = sanitize_resource_id(resource_id)
    except ValueError:
        return _response(400, {"error": "invalid_resource_id"})

    # ── Load context from most recent alerted record ──────────────────────────
    existing      = get_governance_record(TABLE_NAME, resource_id, event_filter="alerted")
    resource_type = existing.get("resource_type", "unknown") if existing else "unknown"
    criticality   = existing.get("criticality",   "UNKNOWN") if existing else "UNKNOWN"
    environment   = existing.get("environment",   "unknown") if existing else "unknown"

    new_status = _ACTION_STATUS[action]

    # ── Persist ───────────────────────────────────────────────────────────────
    put_governance_record(
        TABLE_NAME, resource_id, resource_type, "feedback",
        criticality=criticality,
        compliant=(new_status == "resolved"),
        environment=environment,
        dry_run=DRY_RUN,
        extra={
            "status":  new_status,
            "action":  action,
            "comment": sanitize_log_value(comment),
        },
    )

    logger.info("POST feedback resource=%s action=%s new_status=%s",
                sanitize_log_value(resource_id),
                sanitize_log_value(action),
                new_status)

    return _response(200, {
        "resource_id": resource_id,
        "action":      action,
        "new_status":  new_status,
    })


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_body(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return parsed JSON body, {} for empty/missing body, None for invalid JSON."""
    raw = event.get("body")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _json_default(obj: Any) -> Any:
    """Serialise DynamoDB Decimal types that appear in governance records."""
    if isinstance(obj, decimal.Decimal):
        return int(obj) if obj % 1 == 0 else float(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _response(status_code: int, body: Any) -> Dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(body, default=_json_default),
    }
