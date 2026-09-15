"""
Unit tests for lambdas/shared/utils.py

The handler test files replace `lambdas.shared.utils` / `.constants` in
sys.modules with MagicMocks. To test the *real* module we load it from disk
under its own package name, with the real constants module bound first, so
the relative `from .constants import ...` resolves to genuine values.

Coverage
--------
  - put_governance_metric: two datapoints, dimensions, unknown outcome, API error
  - sanitize_resource_id / sanitize_log_value
  - check_required_tags
  - classify_resource priority rules
"""

import importlib.util
import os
import sys
import types
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

SHARED_DIR = os.path.dirname(os.path.abspath(__file__))
REGION = "eu-west-1"


def _load_real_shared():
    """Load real constants + utils under 'rq_shared.*' without touching the mocked names."""
    pkg = types.ModuleType("rq_shared")
    pkg.__path__ = [SHARED_DIR]
    sys.modules["rq_shared"] = pkg

    for name in ("constants", "utils"):
        spec = importlib.util.spec_from_file_location(
            f"rq_shared.{name}", os.path.join(SHARED_DIR, f"{name}.py")
        )
        mod = importlib.util.module_from_spec(spec)
        mod.__package__ = "rq_shared"
        sys.modules[f"rq_shared.{name}"] = mod
        spec.loader.exec_module(mod)

    return sys.modules["rq_shared.utils"], sys.modules["rq_shared.constants"]


@pytest.fixture()
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)


@pytest.fixture()
def shared():
    utils, constants = _load_real_shared()
    yield utils, constants
    for key in ("rq_shared.utils", "rq_shared.constants", "rq_shared"):
        sys.modules.pop(key, None)


# ── put_governance_metric ─────────────────────────────────────────────────────

@mock_aws
def test_metric_publishes_two_datapoints(aws_env, shared):
    utils, constants = shared
    assert utils.put_governance_metric("alerted", "ec2", "CRITICAL") is True

    cw = boto3.client("cloudwatch", region_name=REGION)
    metrics = cw.list_metrics(Namespace=constants.METRICS_NAMESPACE)["Metrics"]
    assert len(metrics) == 2

    dims = sorted(
        [sorted((d["Name"], d["Value"]) for d in m["Dimensions"]) for m in metrics],
        key=len,
    )
    assert dims[0] == [("Outcome", "alerted")]
    assert dims[1] == [("Criticality", "CRITICAL"), ("Outcome", "alerted"), ("ResourceType", "ec2")]
    assert all(m["MetricName"] == constants.METRIC_GOVERNANCE_EVENTS for m in metrics)


@mock_aws
def test_metric_unknown_outcome_is_skipped(aws_env, shared):
    utils, constants = shared
    assert utils.put_governance_metric("exploded", "ec2") is False
    cw = boto3.client("cloudwatch", region_name=REGION)
    assert cw.list_metrics(Namespace=constants.METRICS_NAMESPACE)["Metrics"] == []


@mock_aws
def test_metric_defaults_for_missing_type_and_criticality(aws_env, shared):
    utils, constants = shared
    utils.put_governance_metric("compliant", "")
    cw = boto3.client("cloudwatch", region_name=REGION)
    metrics = cw.list_metrics(Namespace=constants.METRICS_NAMESPACE)["Metrics"]
    detailed = next(m for m in metrics if len(m["Dimensions"]) == 3)
    values = {d["Name"]: d["Value"] for d in detailed["Dimensions"]}
    assert values["ResourceType"] == "unknown"
    assert values["Criticality"] == "UNKNOWN"


def test_metric_api_error_never_raises(aws_env, shared):
    utils, _ = shared
    with patch.object(utils.boto3, "client") as client:
        client.return_value.put_metric_data.side_effect = RuntimeError("boom")
        assert utils.put_governance_metric("quarantined", "s3") is False


# ── Sanitization ──────────────────────────────────────────────────────────────

def test_sanitize_resource_id_accepts_aws_ids(shared):
    utils, _ = shared
    for rid in ("i-0abc123", "my-bucket.name", "arn:aws:lambda:eu-west-1:123456789012:function:x"):
        assert utils.sanitize_resource_id(rid) == rid


@pytest.mark.parametrize("bad", ["", "i-abc;drop", "../etc/passwd", "a b", "x" * 513])
def test_sanitize_resource_id_rejects_injection(shared, bad):
    utils, _ = shared
    with pytest.raises(ValueError):
        utils.sanitize_resource_id(bad)


def test_sanitize_log_value_strips_newlines(shared):
    utils, _ = shared
    assert utils.sanitize_log_value("a\r\nb\tc") == "ab c"


# ── Tags & classification ─────────────────────────────────────────────────────

def test_check_required_tags(shared):
    utils, constants = shared
    full = [{"Key": k, "Value": "x"} for k in constants.REQUIRED_TAGS]
    assert utils.check_required_tags(full) == (True, [])
    ok, missing = utils.check_required_tags([{"Key": "Owner", "Value": "x"}])
    assert not ok and set(missing) == set(constants.REQUIRED_TAGS) - {"Owner"}


@pytest.mark.parametrize("rtype,tags,expected", [
    ("ec2", [{"Key": "CriticalWorkload", "Value": "TRUE"}], "CRITICAL"),
    ("rds", [], "CRITICAL"),
    ("ec2", [{"Key": "Environment", "Value": "prod"}], "CRITICAL"),
    ("ec2", [{"Key": "Environment", "Value": "dev"}], "NON_CRITICAL"),
    ("s3", [], "NON_CRITICAL"),
])
def test_classify_resource(shared, rtype, tags, expected):
    utils, _ = shared
    assert utils.classify_resource(rtype, tags) == expected
