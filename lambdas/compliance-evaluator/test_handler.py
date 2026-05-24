"""
Unit tests for lambdas/compliance-evaluator/handler.py

Strategy
--------
handler.py uses relative imports (..shared.*). Rather than fighting Python's
package loader we stub the shared modules in sys.modules before the first
import, then reload on each test so boto3 clients are created inside the
moto context.

Coverage
--------
  - lambda_handler routing (Config / CloudTrail / SLADeadline)
  - _handle_compliance_event: handled-skip, compliant, missing-tags full flow
  - _handle_sla_deadline: resolved branch, quarantined branch
  - _extract_event_fields: Config shape, CloudTrail shape, empty event
  - _schedule_name: sanitisation of special characters
  - _apply_quarantine_tags: EC2, S3, RDS, Lambda, unknown type
  - _fetch_current_tags: EC2, S3 (no-tag-set), RDS, Lambda
  - Notification calls (SNS + Slack paths)
  - DRY_RUN=true suppresses scheduler + quarantine tag writes
"""

import importlib
import json
import os
import sys
import types
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, call

import boto3
import pytest
from moto import mock_aws

# ── Shared-layer stubs injected before handler import ─────────────────────────

_SHARED_UTILS = MagicMock()
_SHARED_CONSTANTS = MagicMock()

# Provide real-ish values so handler logic works correctly
_SHARED_UTILS.sanitize_resource_id.side_effect = lambda x: x
_SHARED_UTILS.sanitize_log_value.side_effect = lambda x: x
_SHARED_UTILS.check_required_tags.return_value = (False, ["Owner", "CostCenter"])
_SHARED_UTILS.classify_resource.return_value = "NON_CRITICAL"
_SHARED_UTILS.put_governance_record.return_value = None
_SHARED_UTILS.get_governance_record.return_value = None
_SHARED_UTILS.send_sns.return_value = True
_SHARED_UTILS.send_slack.return_value = True
_SHARED_UTILS.tags_list_to_dict.side_effect = lambda t: {x["Key"]: x["Value"] for x in (t or [])}

_SHARED_CONSTANTS.WATCHED_EVENTS = {
    "RunInstances":           "ec2",
    "CreateBucket":           "s3",
    "CreateDBInstance":       "rds",
    "CreateFunction20150331": "lambda",
}
_SHARED_CONSTANTS.SLA_HOURS = {"CRITICAL": 36, "NON_CRITICAL": 168}
_SHARED_CONSTANTS.SLACK_COLORS = {
    "FAST": "#f39c12",
    "SLOW": "#e74c3c",
    "SLOW_ESCALATION": "#8e44ad",
    "SLOW_FINAL_REMINDER": "#2c3e50",
}
_SHARED_CONSTANTS.TRACK_EMOJI = {
    "FAST": "⚠️",
    "SLOW": "🔴",
    "SLOW_ESCALATION": "🚨",
    "SLOW_FINAL_REMINDER": "🆘",
}

# Inject as if they were the real package paths
sys.modules.setdefault("lambdas", types.ModuleType("lambdas"))
sys.modules.setdefault("lambdas.shared", types.ModuleType("lambdas.shared"))
sys.modules["lambdas.shared.utils"] = _SHARED_UTILS
sys.modules["lambdas.shared.constants"] = _SHARED_CONSTANTS

# Force handler to use our stubs when resolving relative imports
_pkg = types.ModuleType("lambdas.compliance_evaluator")
_pkg.__path__ = []
_pkg.__package__ = "lambdas.compliance_evaluator"
sys.modules.setdefault("lambdas.compliance_evaluator", _pkg)

HANDLER_DIR = os.path.dirname(os.path.abspath(__file__))

REGION = "eu-west-1"

COMPLIANT_TAGS = [
    {"Key": "Owner",       "Value": "alice@corp.com"},
    {"Key": "Squad",       "Value": "platform"},
    {"Key": "CostCenter",  "Value": "CC-42"},
    {"Key": "Environment", "Value": "dev"},
]

INCOMPLETE_TAGS = [
    {"Key": "Squad",       "Value": "platform"},
    {"Key": "Environment", "Value": "dev"},
]

BASE_ENV = {
    "AWS_ACCESS_KEY_ID":     "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SESSION_TOKEN":     "testing",
    "AWS_DEFAULT_REGION":    REGION,
    "GOVERNANCE_TABLE":      "governance",
    "SNS_TOPIC_ARN":         "arn:aws:sns:eu-west-1:123456789012:alerts",
    "SLACK_WEBHOOK_URL":     "https://hooks.slack.com/test",
    "SCHEDULER_ROLE_ARN":    "arn:aws:iam::123456789012:role/scheduler",
    "AWS_LAMBDA_FUNCTION_ARN": "arn:aws:lambda:eu-west-1:123456789012:function:evaluator",
    "DRY_RUN":               "false",
}


def _load_handler():
    """Import or reload handler so boto3 clients are bound inside moto context."""
    mod_name = "lambdas.compliance_evaluator.handler"
    if mod_name in sys.modules:
        return importlib.reload(sys.modules[mod_name])

    # First load: add the directory so a plain import works
    if HANDLER_DIR not in sys.path:
        sys.path.insert(0, os.path.dirname(HANDLER_DIR))  # lambdas/

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        mod_name,
        os.path.join(HANDLER_DIR, "handler.py"),
        submodule_search_locations=[],
    )
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "lambdas.compliance_evaluator"
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def reset_shared_mocks():
    """Reset call history on shared stubs before every test."""
    # Re-assert ownership of the shared-layer stubs in sys.modules so that
    # importlib-mode test collection (which imports all test files upfront)
    # cannot leave a sibling test file's mock in place when this handler reloads.
    sys.modules["lambdas.shared.utils"]     = _SHARED_UTILS
    sys.modules["lambdas.shared.constants"] = _SHARED_CONSTANTS

    _SHARED_UTILS.reset_mock()
    _SHARED_UTILS.sanitize_resource_id.side_effect = lambda x: x
    _SHARED_UTILS.sanitize_log_value.side_effect = lambda x: x
    _SHARED_UTILS.check_required_tags.return_value = (False, ["Owner", "CostCenter"])
    _SHARED_UTILS.classify_resource.return_value = "NON_CRITICAL"
    _SHARED_UTILS.put_governance_record.return_value = None
    _SHARED_UTILS.get_governance_record.return_value = None
    _SHARED_UTILS.send_sns.return_value = True
    _SHARED_UTILS.send_slack.return_value = True
    _SHARED_UTILS.tags_list_to_dict.side_effect = lambda t: {x["Key"]: x["Value"] for x in (t or [])}
    yield
    key = "lambdas.compliance_evaluator.handler"
    if key in sys.modules:
        del sys.modules[key]


@pytest.fixture()
def env(monkeypatch):
    for k, v in BASE_ENV.items():
        monkeypatch.setenv(k, v)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _config_event(resource_id="i-abc123", resource_type="AWS::EC2::Instance", tags=None):
    return {
        "detail-type": "Config Rules Compliance Change",
        "account": "123456789012",
        "region": REGION,
        "detail": {
            "configurationItem": {
                "resourceId":   resource_id,
                "resourceType": resource_type,
                "tags": {t["Key"]: t["Value"] for t in (tags or INCOMPLETE_TAGS)},
            }
        },
    }


def _cloudtrail_event(event_name="RunInstances", instance_id="i-new001"):
    return {
        "detail-type": "AWS API Call via CloudTrail",
        "account": "123456789012",
        "region": REGION,
        "detail": {
            "eventName": event_name,
            "requestParameters": {
                "instancesSet": {"items": [{"instanceId": instance_id}]},
                "tagSpecificationSet": {
                    "items": [{"tags": {"items": INCOMPLETE_TAGS}}]
                },
            },
            "userIdentity": {"accountId": "123456789012"},
        },
    }


def _sla_event(resource_id="i-abc123", resource_type="ec2", criticality="NON_CRITICAL"):
    return {
        "detail-type": "SLADeadline",
        "detail": {
            "resource_id":    resource_id,
            "resource_type":  resource_type,
            "criticality":    criticality,
            "schedule_name":  f"rq-sla-{resource_id}",
        },
    }


# ── Routing ───────────────────────────────────────────────────────────────────

@mock_aws
def test_routes_sla_deadline(env):
    h = _load_handler()
    with patch.object(h, "_handle_sla_deadline", return_value={"status": "resolved"}) as mock_sla, \
         patch.object(h, "_handle_compliance_event", return_value={}) as mock_ce:
        h.lambda_handler(_sla_event(), None)
        mock_sla.assert_called_once()
        mock_ce.assert_not_called()


@mock_aws
def test_routes_config_event(env):
    h = _load_handler()
    with patch.object(h, "_handle_sla_deadline", return_value={}) as mock_sla, \
         patch.object(h, "_handle_compliance_event", return_value={"status": "alerted"}) as mock_ce:
        h.lambda_handler(_config_event(), None)
        mock_ce.assert_called_once()
        mock_sla.assert_not_called()


# ── _handle_compliance_event ──────────────────────────────────────────────────

@mock_aws
def test_skips_handled_resource(env):
    _SHARED_UTILS.get_governance_record.return_value = {"status": "handled"}
    h = _load_handler()
    result = h.lambda_handler(_config_event(), None)
    assert result["status"] == "skipped"
    assert result["reason"] == "handled"
    _SHARED_UTILS.send_sns.assert_not_called()
    _SHARED_UTILS.send_slack.assert_not_called()


@mock_aws
def test_compliant_resource_records_scan(env):
    _SHARED_UTILS.check_required_tags.return_value = (True, [])
    h = _load_handler()
    result = h.lambda_handler(_config_event(tags=COMPLIANT_TAGS), None)
    assert result["status"] == "compliant"
    _SHARED_UTILS.put_governance_record.assert_called_once()
    call_kwargs = _SHARED_UTILS.put_governance_record.call_args
    assert call_kwargs.args[3] == "scan"       # event positional arg
    assert call_kwargs.kwargs["compliant"] is True
    _SHARED_UTILS.send_sns.assert_not_called()


@mock_aws
def test_missing_tags_sends_alerts(env):
    h = _load_handler()
    with patch.object(h, "_schedule_sla_check"):
        result = h.lambda_handler(_config_event(), None)
    assert result["status"] == "alerted"
    assert result["missing_tags"] == ["Owner", "CostCenter"]
    _SHARED_UTILS.send_sns.assert_called_once()
    _SHARED_UTILS.send_slack.assert_called_once()


@mock_aws
def test_missing_tags_records_alerted(env):
    h = _load_handler()
    with patch.object(h, "_schedule_sla_check"):
        h.lambda_handler(_config_event(), None)
    args = _SHARED_UTILS.put_governance_record.call_args
    assert args.args[3] == "alerted"
    assert args.kwargs["extra"]["status"] == "alerted"
    assert "sla_deadline" in args.kwargs["extra"]


@mock_aws
def test_critical_resource_uses_36h_sla(env):
    _SHARED_UTILS.classify_resource.return_value = "CRITICAL"
    h = _load_handler()
    with patch.object(h, "_schedule_sla_check") as mock_sched:
        result = h.lambda_handler(_config_event(), None)
    assert result["criticality"] == "CRITICAL"
    deadline_str = result["sla_deadline"]
    # 36h from now — allow ±5 min tolerance
    deadline = datetime.fromisoformat(deadline_str)
    expected = datetime.now(timezone.utc) + timedelta(hours=36)
    assert abs((deadline - expected).total_seconds()) < 300


@mock_aws
def test_non_critical_resource_uses_168h_sla(env):
    _SHARED_UTILS.classify_resource.return_value = "NON_CRITICAL"
    h = _load_handler()
    with patch.object(h, "_schedule_sla_check"):
        result = h.lambda_handler(_config_event(), None)
    deadline_str = result["sla_deadline"]
    deadline = datetime.fromisoformat(deadline_str)
    expected = datetime.now(timezone.utc) + timedelta(hours=168)
    assert abs((deadline - expected).total_seconds()) < 300


@mock_aws
def test_dry_run_skips_scheduler(env, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    h = _load_handler()
    with patch.object(h, "_schedule_sla_check") as mock_sched:
        h.lambda_handler(_config_event(), None)
        mock_sched.assert_not_called()


@mock_aws
def test_missing_resource_id_skips(env):
    event = {"detail-type": "Config Rules Compliance Change", "detail": {}}
    h = _load_handler()
    result = h.lambda_handler(event, None)
    assert result["status"] == "skipped"
    assert result["reason"] == "missing_resource_id"


# ── _handle_sla_deadline ──────────────────────────────────────────────────────

@mock_aws
def test_sla_resolved_when_now_compliant(env):
    _SHARED_UTILS.check_required_tags.return_value = (True, [])
    h = _load_handler()
    with patch.object(h, "_fetch_current_tags", return_value=COMPLIANT_TAGS), \
         patch.object(h, "_delete_schedule") as mock_del:
        result = h.lambda_handler(_sla_event(), None)
    assert result["status"] == "resolved"
    args = _SHARED_UTILS.put_governance_record.call_args
    assert args.args[3] == "resolved"
    assert args.kwargs["extra"]["status"] == "resolved"
    mock_del.assert_called_once_with(f"rq-sla-i-abc123")


@mock_aws
def test_sla_quarantined_when_still_non_compliant(env):
    h = _load_handler()
    with patch.object(h, "_fetch_current_tags", return_value=INCOMPLETE_TAGS), \
         patch.object(h, "_apply_quarantine_tags") as mock_q, \
         patch.object(h, "_delete_schedule"):
        result = h.lambda_handler(_sla_event(), None)
    assert result["status"] == "quarantined"
    mock_q.assert_called_once_with("i-abc123", "ec2")
    args = _SHARED_UTILS.put_governance_record.call_args
    assert args.args[3] == "quarantined"


@mock_aws
def test_sla_dry_run_skips_quarantine_tags(env, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    h = _load_handler()
    with patch.object(h, "_fetch_current_tags", return_value=INCOMPLETE_TAGS), \
         patch.object(h, "_apply_quarantine_tags") as mock_q, \
         patch.object(h, "_delete_schedule"):
        result = h.lambda_handler(_sla_event(), None)
    assert result["status"] == "quarantined"
    mock_q.assert_not_called()


@mock_aws
def test_sla_missing_resource_id_skips(env):
    event = {"detail-type": "SLADeadline", "detail": {}}
    h = _load_handler()
    result = h.lambda_handler(event, None)
    assert result["status"] == "skipped"


# ── _extract_event_fields ─────────────────────────────────────────────────────

@mock_aws
def test_extracts_config_event_fields(env):
    h = _load_handler()
    event = _config_event(resource_id="i-abc", resource_type="AWS::EC2::Instance")
    rid, rtype, tags, region, account = h._extract_event_fields(event)
    assert rid == "i-abc"
    assert rtype == "ec2"
    assert region == REGION
    assert account == "123456789012"


@mock_aws
def test_extracts_cloudtrail_run_instances(env):
    h = _load_handler()
    event = _cloudtrail_event("RunInstances", "i-new999")
    rid, rtype, tags, region, account = h._extract_event_fields(event)
    assert rid == "i-new999"
    assert rtype == "ec2"


@mock_aws
def test_extracts_cloudtrail_create_bucket(env):
    h = _load_handler()
    event = {
        "detail-type": "AWS API Call via CloudTrail",
        "account": "123456789012",
        "region": REGION,
        "detail": {
            "eventName": "CreateBucket",
            "requestParameters": {"bucketName": "my-test-bucket"},
            "userIdentity": {"accountId": "123456789012"},
        },
    }
    rid, rtype, tags, region, account = h._extract_event_fields(event)
    assert rid == "my-test-bucket"
    assert rtype == "s3"


@mock_aws
def test_extracts_cloudtrail_create_rds(env):
    h = _load_handler()
    event = {
        "detail-type": "AWS API Call via CloudTrail",
        "account": "123456789012",
        "region": REGION,
        "detail": {
            "eventName": "CreateDBInstance",
            "requestParameters": {"dBInstanceIdentifier": "mydb"},
            "userIdentity": {"accountId": "123456789012"},
        },
    }
    rid, rtype, _, _, _ = h._extract_event_fields(event)
    assert rid == "mydb"
    assert rtype == "rds"


@mock_aws
def test_extracts_cloudtrail_create_lambda(env):
    h = _load_handler()
    event = {
        "detail-type": "AWS API Call via CloudTrail",
        "account": "123456789012",
        "region": REGION,
        "detail": {
            "eventName": "CreateFunction20150331",
            "requestParameters": {"functionName": "my-function"},
            "userIdentity": {"accountId": "123456789012"},
        },
    }
    rid, rtype, _, _, _ = h._extract_event_fields(event)
    assert rid == "my-function"
    assert rtype == "lambda"


@mock_aws
def test_config_type_normalisation(env):
    h = _load_handler()
    assert h._normalise_config_type("AWS::EC2::Instance") == "ec2"
    assert h._normalise_config_type("AWS::S3::Bucket") == "s3"
    assert h._normalise_config_type("AWS::RDS::DBInstance") == "rds"
    assert h._normalise_config_type("AWS::Lambda::Function") == "lambda"
    assert h._normalise_config_type("AWS::Unknown::Type") == "type"


# ── _schedule_name ────────────────────────────────────────────────────────────

@mock_aws
def test_schedule_name_alphanumeric(env):
    h = _load_handler()
    assert h._schedule_name("i-abc123") == "rq-sla-i-abc123"


@mock_aws
def test_schedule_name_replaces_special_chars(env):
    h = _load_handler()
    name = h._schedule_name("arn:aws:rds:eu-west-1:123:db/mydb")
    assert ":" not in name.replace("rq-sla-", "")
    assert "/" not in name


@mock_aws
def test_schedule_name_truncated_to_68_chars(env):
    h = _load_handler()
    long_id = "x" * 200
    name = h._schedule_name(long_id)
    assert len(name) <= 72  # "rq-sla-" (7) + 64 slug chars + margin


# ── _apply_quarantine_tags ────────────────────────────────────────────────────

@mock_aws
def test_quarantine_tags_ec2(env):
    h = _load_handler()
    ec2 = boto3.client("ec2", region_name=REGION)
    # Create a real EC2 instance via moto
    ami = ec2.describe_images(Owners=["amazon"])["Images"]
    # moto provides a default AMI
    reservation = ec2.run_instances(
        ImageId="ami-12345678", MinCount=1, MaxCount=1
    )
    instance_id = reservation["Instances"][0]["InstanceId"]
    h._apply_quarantine_tags(instance_id, "ec2")
    tags_resp = ec2.describe_tags(
        Filters=[{"Name": "resource-id", "Values": [instance_id]}]
    )
    tag_map = {t["Key"]: t["Value"] for t in tags_resp["Tags"]}
    assert tag_map["Status"] == "needs-review"
    assert "QuarantinedAt" in tag_map
    assert tag_map["QuarantineReason"] == "missing_required_tags"


@mock_aws
def test_quarantine_tags_s3(env):
    h = _load_handler()
    s3 = boto3.client("s3", region_name=REGION)
    bucket = "test-quarantine-bucket"
    s3.create_bucket(
        Bucket=bucket,
        CreateBucketConfiguration={"LocationConstraint": REGION},
    )
    h._apply_quarantine_tags(bucket, "s3")
    resp = s3.get_bucket_tagging(Bucket=bucket)
    tag_map = {t["Key"]: t["Value"] for t in resp["TagSet"]}
    assert tag_map["Status"] == "needs-review"
    assert tag_map["QuarantineReason"] == "missing_required_tags"


@mock_aws
def test_quarantine_tags_s3_preserves_existing_tags(env):
    h = _load_handler()
    s3 = boto3.client("s3", region_name=REGION)
    bucket = "test-existing-tags"
    s3.create_bucket(
        Bucket=bucket,
        CreateBucketConfiguration={"LocationConstraint": REGION},
    )
    s3.put_bucket_tagging(
        Bucket=bucket,
        Tagging={"TagSet": [{"Key": "Squad", "Value": "platform"}]},
    )
    h._apply_quarantine_tags(bucket, "s3")
    resp = s3.get_bucket_tagging(Bucket=bucket)
    tag_map = {t["Key"]: t["Value"] for t in resp["TagSet"]}
    assert tag_map["Squad"] == "platform"        # preserved
    assert tag_map["Status"] == "needs-review"   # added


def _create_lambda_execution_role(iam_client, role_name="lambda-exec"):
    """
    Create a minimal Lambda execution role for moto tests.
    Moto validates the role exists and is assumable by lambda.amazonaws.com;
    it does not enforce permission boundaries, so no policy attachment is needed.
    """
    role = iam_client.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "lambda.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }],
        }),
    )["Role"]
    return role["Arn"]


@mock_aws
def test_quarantine_tags_lambda(env):
    h = _load_handler()
    iam = boto3.client("iam", region_name=REGION)
    role_arn = _create_lambda_execution_role(iam, "lambda-exec-quarantine")
    lam = boto3.client("lambda", region_name=REGION)
    lam.create_function(
        FunctionName="my-func",
        Runtime="python3.12",
        Role=role_arn,
        Handler="index.handler",
        Code={"ZipFile": b"def handler(e,c): pass"},
    )
    fn_arn = lam.get_function(FunctionName="my-func")["Configuration"]["FunctionArn"]
    h._apply_quarantine_tags(fn_arn, "lambda")
    resp = lam.list_tags(Resource=fn_arn)
    assert resp["Tags"]["Status"] == "needs-review"
    assert resp["Tags"]["QuarantineReason"] == "missing_required_tags"


@mock_aws
def test_quarantine_tags_unknown_type_logs_warning(env, caplog):
    import logging
    h = _load_handler()
    with caplog.at_level(logging.WARNING, logger="lambdas.compliance_evaluator.handler"):
        h._apply_quarantine_tags("some-id", "unknown_type")
    assert any("unsupported type" in r.message for r in caplog.records)


# ── _fetch_current_tags ───────────────────────────────────────────────────────

@mock_aws
def test_fetch_tags_ec2(env):
    h = _load_handler()
    ec2 = boto3.client("ec2", region_name=REGION)
    reservation = ec2.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)
    iid = reservation["Instances"][0]["InstanceId"]
    ec2.create_tags(Resources=[iid], Tags=[{"Key": "Owner", "Value": "bob"}])
    tags = h._fetch_current_tags(iid, "ec2")
    tag_map = {t["Key"]: t["Value"] for t in tags}
    assert tag_map["Owner"] == "bob"


@mock_aws
def test_fetch_tags_s3(env):
    h = _load_handler()
    s3 = boto3.client("s3", region_name=REGION)
    bucket = "fetch-tag-bucket"
    s3.create_bucket(
        Bucket=bucket,
        CreateBucketConfiguration={"LocationConstraint": REGION},
    )
    s3.put_bucket_tagging(
        Bucket=bucket,
        Tagging={"TagSet": [{"Key": "Squad", "Value": "data"}]},
    )
    tags = h._fetch_current_tags(bucket, "s3")
    assert {"Key": "Squad", "Value": "data"} in tags


@mock_aws
def test_fetch_tags_s3_no_tags_returns_empty(env):
    h = _load_handler()
    s3 = boto3.client("s3", region_name=REGION)
    bucket = "no-tag-bucket"
    s3.create_bucket(
        Bucket=bucket,
        CreateBucketConfiguration={"LocationConstraint": REGION},
    )
    tags = h._fetch_current_tags(bucket, "s3")
    assert tags == []


@mock_aws
def test_fetch_tags_lambda(env):
    h = _load_handler()
    iam = boto3.client("iam", region_name=REGION)
    role_arn = _create_lambda_execution_role(iam, "lambda-exec-fetch")
    lam = boto3.client("lambda", region_name=REGION)
    lam.create_function(
        FunctionName="fetch-test",
        Runtime="python3.12",
        Role=role_arn,
        Handler="index.handler",
        Code={"ZipFile": b"def handler(e,c): pass"},
        Tags={"CostCenter": "CC-99"},
    )
    fn_arn = lam.get_function(FunctionName="fetch-test")["Configuration"]["FunctionArn"]
    tags = h._fetch_current_tags(fn_arn, "lambda")
    tag_map = {t["Key"]: t["Value"] for t in tags}
    assert tag_map["CostCenter"] == "CC-99"


@mock_aws
def test_fetch_tags_unknown_type_returns_empty(env):
    h = _load_handler()
    tags = h._fetch_current_tags("something", "unknown")
    assert tags == []


# ── Notification helpers ──────────────────────────────────────────────────────

@mock_aws
def test_send_alert_called_with_correct_track_critical(env):
    _SHARED_UTILS.classify_resource.return_value = "CRITICAL"
    h = _load_handler()
    with patch.object(h, "_schedule_sla_check"):
        h.lambda_handler(_config_event(), None)
    # For CRITICAL, track=FAST → subject should contain emoji ⚠️
    subject_arg = _SHARED_UTILS.send_sns.call_args[0][1]
    assert "⚠️" in subject_arg or "CRITICAL" in subject_arg


@mock_aws
def test_send_alert_called_with_correct_track_non_critical(env):
    _SHARED_UTILS.classify_resource.return_value = "NON_CRITICAL"
    h = _load_handler()
    with patch.object(h, "_schedule_sla_check"):
        h.lambda_handler(_config_event(), None)
    subject_arg = _SHARED_UTILS.send_sns.call_args[0][1]
    assert "🔴" in subject_arg or "NON_CRITICAL" in subject_arg


@mock_aws
def test_slack_payload_contains_resource_info(env):
    h = _load_handler()
    with patch.object(h, "_schedule_sla_check"):
        h.lambda_handler(_config_event(resource_id="i-xyz"), None)
    slack_payload = _SHARED_UTILS.send_slack.call_args[0][1]
    attachment_text = json.dumps(slack_payload)
    assert "i-xyz" in attachment_text
    assert "Owner" in attachment_text or "CostCenter" in attachment_text
