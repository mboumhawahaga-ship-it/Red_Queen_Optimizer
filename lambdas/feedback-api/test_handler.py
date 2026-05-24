"""
Unit tests for lambdas/feedback-api/handler.py

Strategy
--------
handler.py makes no direct boto3 calls — every AWS interaction goes through
shared/utils.py. We stub the shared layer in sys.modules before the first
import (same pattern as compliance-evaluator tests) and reset mock state
before each test.

Coverage
--------
  GET  /feedback
    - 200 with record
    - 200 with Decimal values serialised correctly
    - 404 when record not found
    - 400 missing resource_id
    - 400 null queryStringParameters
    - 400 invalid (injection) resource_id

  POST /feedback
    - approve  → status=handled
    - tag      → status=resolved
    - reject   → status=alerted
    - context (resource_type / criticality / environment) lifted from alerted record
    - fallback to "unknown" defaults when no alerted record exists
    - 400 invalid action (lists valid actions in response)
    - 400 missing resource_id
    - 400 missing action
    - 400 malformed JSON body
    - 400 invalid (injection) resource_id
    - comment field is sanitized before persistence
    - DRY_RUN flag forwarded to put_governance_record

  General
    - 405 for unsupported HTTP methods
"""

import decimal
import importlib
import importlib.util
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ── Stub the shared layer before any handler import ──────────────────────────

_SHARED_UTILS      = MagicMock()
_SHARED_CONSTANTS  = MagicMock()

_SHARED_CONSTANTS.VALID_ACTIONS = {"approve", "reject", "tag"}

_SHARED_UTILS.sanitize_resource_id.side_effect = lambda x: x
_SHARED_UTILS.sanitize_log_value.side_effect   = lambda x: x
_SHARED_UTILS.get_governance_record.return_value = None
_SHARED_UTILS.put_governance_record.return_value = None

sys.modules.setdefault("lambdas", types.ModuleType("lambdas"))
sys.modules.setdefault("lambdas.shared", types.ModuleType("lambdas.shared"))
sys.modules["lambdas.shared.utils"]      = _SHARED_UTILS
sys.modules["lambdas.shared.constants"]  = _SHARED_CONSTANTS

_pkg = types.ModuleType("lambdas.feedback_api")
_pkg.__path__    = []
_pkg.__package__ = "lambdas.feedback_api"
sys.modules.setdefault("lambdas.feedback_api", _pkg)

HANDLER_DIR = os.path.dirname(os.path.abspath(__file__))

BASE_ENV = {
    "GOVERNANCE_TABLE": "governance",
    "DRY_RUN":          "false",
}


def _load_handler():
    mod_name = "lambdas.feedback_api.handler"
    if mod_name in sys.modules:
        return importlib.reload(sys.modules[mod_name])

    spec = importlib.util.spec_from_file_location(
        mod_name,
        os.path.join(HANDLER_DIR, "handler.py"),
        submodule_search_locations=[],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "lambdas.feedback_api"
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def reset_mocks(monkeypatch):
    for k, v in BASE_ENV.items():
        monkeypatch.setenv(k, v)

    # Re-assert ownership of the shared-layer stubs in sys.modules.
    sys.modules["lambdas.shared.utils"]     = _SHARED_UTILS
    sys.modules["lambdas.shared.constants"] = _SHARED_CONSTANTS

    _SHARED_UTILS.reset_mock()
    _SHARED_UTILS.sanitize_resource_id.side_effect = lambda x: x
    _SHARED_UTILS.sanitize_log_value.side_effect   = lambda x: x
    _SHARED_UTILS.get_governance_record.return_value = None
    _SHARED_UTILS.put_governance_record.return_value = None

    yield

    key = "lambdas.feedback_api.handler"
    if key in sys.modules:
        del sys.modules[key]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_event(resource_id="i-abc123"):
    return {
        "httpMethod": "GET",
        "queryStringParameters": {"resource_id": resource_id},
        "body": None,
    }


def _post_event(resource_id="i-abc123", action="approve", comment="", body_override=None):
    payload = body_override if body_override is not None else {
        "resource_id": resource_id,
        "action":      action,
        "comment":     comment,
    }
    return {
        "httpMethod": "POST",
        "queryStringParameters": None,
        "body": json.dumps(payload),
    }


def _existing_record(resource_type="ec2", criticality="CRITICAL", environment="prod"):
    return {
        "resource_id":    "i-abc123",
        "resource_type":  resource_type,
        "criticality":    criticality,
        "environment":    environment,
        "event":          "alerted",
        "status":         "alerted",
        "scan_timestamp": "2026-05-24T10:00:00+00:00",
        "sla_deadline":   "2026-05-25T22:00:00+00:00",
    }


# ══════════════════════════════════════════════════════════════════════════════
# GET /feedback
# ══════════════════════════════════════════════════════════════════════════════

def test_get_returns_record():
    _SHARED_UTILS.get_governance_record.return_value = _existing_record()
    h = _load_handler()
    resp = h.lambda_handler(_get_event(), None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["resource_id"] == "i-abc123"
    assert body["status"] == "alerted"


def test_get_decimal_values_serialised():
    record = _existing_record()
    record["ttl_expiry"] = decimal.Decimal("1748000000")
    record["cost"]       = decimal.Decimal("3.14")
    _SHARED_UTILS.get_governance_record.return_value = record
    h = _load_handler()
    resp = h.lambda_handler(_get_event(), None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["ttl_expiry"] == 1748000000
    assert body["cost"] == 3.14


def test_get_not_found_returns_404():
    _SHARED_UTILS.get_governance_record.return_value = None
    h = _load_handler()
    resp = h.lambda_handler(_get_event(), None)
    assert resp["statusCode"] == 404
    body = json.loads(resp["body"])
    assert body["error"] == "not_found"
    assert body["resource_id"] == "i-abc123"


def test_get_missing_resource_id_returns_400():
    h = _load_handler()
    event = {"httpMethod": "GET", "queryStringParameters": {}, "body": None}
    resp = h.lambda_handler(event, None)
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"] == "missing_resource_id"


def test_get_null_query_params_returns_400():
    h = _load_handler()
    event = {"httpMethod": "GET", "queryStringParameters": None, "body": None}
    resp = h.lambda_handler(event, None)
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"] == "missing_resource_id"


def test_get_invalid_resource_id_returns_400():
    _SHARED_UTILS.sanitize_resource_id.side_effect = ValueError("invalid")
    h = _load_handler()
    resp = h.lambda_handler(_get_event(resource_id="../../etc/passwd"), None)
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"] == "invalid_resource_id"


def test_get_calls_governance_record_with_table_and_id():
    _SHARED_UTILS.get_governance_record.return_value = _existing_record()
    h = _load_handler()
    h.lambda_handler(_get_event(resource_id="i-xyz999"), None)
    _SHARED_UTILS.get_governance_record.assert_called_once_with("governance", "i-xyz999")


# ══════════════════════════════════════════════════════════════════════════════
# POST /feedback — action routing
# ══════════════════════════════════════════════════════════════════════════════

def test_post_approve_sets_handled():
    h = _load_handler()
    resp = h.lambda_handler(_post_event(action="approve"), None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["new_status"] == "handled"
    assert body["action"] == "approve"


def test_post_tag_sets_resolved():
    h = _load_handler()
    resp = h.lambda_handler(_post_event(action="tag"), None)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["new_status"] == "resolved"


def test_post_reject_sets_alerted():
    h = _load_handler()
    resp = h.lambda_handler(_post_event(action="reject"), None)
    assert resp["statusCode"] == 200
    assert json.loads(resp["body"])["new_status"] == "alerted"


def test_post_approve_records_compliant_false():
    """approve → handled, not actually compliant (tags still missing)."""
    h = _load_handler()
    h.lambda_handler(_post_event(action="approve"), None)
    call_kwargs = _SHARED_UTILS.put_governance_record.call_args.kwargs
    assert call_kwargs["compliant"] is False


def test_post_tag_records_compliant_true():
    """tag → resolved → compliant=True (owner asserts tags added)."""
    h = _load_handler()
    h.lambda_handler(_post_event(action="tag"), None)
    call_kwargs = _SHARED_UTILS.put_governance_record.call_args.kwargs
    assert call_kwargs["compliant"] is True


# ══════════════════════════════════════════════════════════════════════════════
# POST /feedback — context inheritance from existing record
# ══════════════════════════════════════════════════════════════════════════════

def test_post_inherits_context_from_alerted_record():
    _SHARED_UTILS.get_governance_record.return_value = _existing_record(
        resource_type="rds", criticality="CRITICAL", environment="prod"
    )
    h = _load_handler()
    h.lambda_handler(_post_event(), None)

    args   = _SHARED_UTILS.put_governance_record.call_args.args
    kwargs = _SHARED_UTILS.put_governance_record.call_args.kwargs

    assert args[2] == "rds"           # resource_type positional
    assert kwargs["criticality"]  == "CRITICAL"
    assert kwargs["environment"]  == "prod"


def test_post_queries_alerted_filter_for_context():
    _SHARED_UTILS.get_governance_record.return_value = None
    h = _load_handler()
    h.lambda_handler(_post_event(), None)
    _SHARED_UTILS.get_governance_record.assert_called_once_with(
        "governance", "i-abc123", event_filter="alerted"
    )


def test_post_uses_unknown_defaults_when_no_existing_record():
    _SHARED_UTILS.get_governance_record.return_value = None
    h = _load_handler()
    h.lambda_handler(_post_event(), None)

    args   = _SHARED_UTILS.put_governance_record.call_args.args
    kwargs = _SHARED_UTILS.put_governance_record.call_args.kwargs

    assert args[2] == "unknown"        # resource_type
    assert kwargs["criticality"]  == "UNKNOWN"
    assert kwargs["environment"]  == "unknown"


def test_post_writes_event_type_feedback():
    h = _load_handler()
    h.lambda_handler(_post_event(), None)
    args = _SHARED_UTILS.put_governance_record.call_args.args
    assert args[3] == "feedback"       # event positional arg


def test_post_extra_contains_action_and_status():
    h = _load_handler()
    h.lambda_handler(_post_event(action="approve"), None)
    extra = _SHARED_UTILS.put_governance_record.call_args.kwargs["extra"]
    assert extra["action"]  == "approve"
    assert extra["status"]  == "handled"


# ══════════════════════════════════════════════════════════════════════════════
# POST /feedback — comment sanitization
# ══════════════════════════════════════════════════════════════════════════════

def test_post_comment_is_sanitized():
    dirty  = "legit comment\ninjected log line"
    clean  = "legit comment injected log line"
    _SHARED_UTILS.sanitize_log_value.side_effect = lambda v: v.replace("\n", " ")
    h = _load_handler()
    h.lambda_handler(_post_event(comment=dirty), None)
    extra = _SHARED_UTILS.put_governance_record.call_args.kwargs["extra"]
    assert extra["comment"] == clean


def test_post_empty_comment_stored_as_empty_string():
    h = _load_handler()
    h.lambda_handler(_post_event(comment=""), None)
    extra = _SHARED_UTILS.put_governance_record.call_args.kwargs["extra"]
    assert extra["comment"] == ""


# ══════════════════════════════════════════════════════════════════════════════
# POST /feedback — validation failures
# ══════════════════════════════════════════════════════════════════════════════

def test_post_invalid_action_returns_400():
    h = _load_handler()
    resp = h.lambda_handler(_post_event(action="delete"), None)
    assert resp["statusCode"] == 400
    body = json.loads(resp["body"])
    assert body["error"] == "invalid_action"
    assert body["received"] == "delete"
    assert set(body["valid"]) == {"approve", "reject", "tag"}


def test_post_missing_resource_id_returns_400():
    h = _load_handler()
    event = _post_event(body_override={"action": "approve"})
    resp = h.lambda_handler(event, None)
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"] == "missing_resource_id"


def test_post_missing_action_returns_400():
    h = _load_handler()
    event = _post_event(body_override={"resource_id": "i-abc123"})
    resp = h.lambda_handler(event, None)
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"] == "missing_action"


def test_post_invalid_json_returns_400():
    h = _load_handler()
    event = {"httpMethod": "POST", "body": "not-json{{{"}
    resp = h.lambda_handler(event, None)
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"] == "invalid_json"


def test_post_invalid_resource_id_returns_400():
    _SHARED_UTILS.sanitize_resource_id.side_effect = ValueError("invalid")
    h = _load_handler()
    resp = h.lambda_handler(_post_event(resource_id="../../etc/passwd"), None)
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"] == "invalid_resource_id"


def test_post_no_body_returns_missing_resource_id():
    """Null/absent body is treated as empty dict → missing_resource_id, not invalid_json."""
    h = _load_handler()
    event = {"httpMethod": "POST", "body": None}
    resp = h.lambda_handler(event, None)
    assert resp["statusCode"] == 400
    assert json.loads(resp["body"])["error"] == "missing_resource_id"


# ══════════════════════════════════════════════════════════════════════════════
# DRY_RUN forwarding
# ══════════════════════════════════════════════════════════════════════════════

def test_dry_run_false_forwarded_to_put_governance_record():
    h = _load_handler()    # BASE_ENV has DRY_RUN=false
    h.lambda_handler(_post_event(), None)
    assert _SHARED_UTILS.put_governance_record.call_args.kwargs["dry_run"] is False


def test_dry_run_true_forwarded_to_put_governance_record(monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    h = _load_handler()
    h.lambda_handler(_post_event(), None)
    assert _SHARED_UTILS.put_governance_record.call_args.kwargs["dry_run"] is True


# ══════════════════════════════════════════════════════════════════════════════
# HTTP method routing
# ══════════════════════════════════════════════════════════════════════════════

def test_put_returns_405():
    h = _load_handler()
    resp = h.lambda_handler({"httpMethod": "PUT", "body": None}, None)
    assert resp["statusCode"] == 405
    body = json.loads(resp["body"])
    assert body["error"] == "method_not_allowed"
    assert "GET" in body["allowed"]
    assert "POST" in body["allowed"]


def test_delete_returns_405():
    h = _load_handler()
    resp = h.lambda_handler({"httpMethod": "DELETE", "body": None}, None)
    assert resp["statusCode"] == 405


# ══════════════════════════════════════════════════════════════════════════════
# Response structure
# ══════════════════════════════════════════════════════════════════════════════

def test_response_has_content_type_header():
    _SHARED_UTILS.get_governance_record.return_value = _existing_record()
    h = _load_handler()
    resp = h.lambda_handler(_get_event(), None)
    assert resp["headers"]["Content-Type"] == "application/json"


def test_response_has_cors_header():
    _SHARED_UTILS.get_governance_record.return_value = _existing_record()
    h = _load_handler()
    resp = h.lambda_handler(_get_event(), None)
    assert resp["headers"]["Access-Control-Allow-Origin"] == "*"
