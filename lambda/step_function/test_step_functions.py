"""
Tests end-to-end — Red Queen Tiered Alert System

Couvre :
  notify    — email SNS + Slack, reminder_number, stockage taskToken
  check     — compliant / non-compliant / not_found
  remediate — DRY_RUN, EC2 stop+tag, S3 block+tag, Lambda throttle
  FAST flux — approve handled, reject → remediate, timeout → remediate
  SLOW flux — compliant après 36h, escalation → handled, escalation → remediate
  feedback  → SendTaskSuccess reprend la Step Function
"""

import os
import sys
import json
import importlib
import pytest
import boto3
from unittest.mock import patch, MagicMock, call
from moto import mock_aws

REGION     = "eu-west-1"
ACCOUNT_ID = "123456789012"
TABLE_NAME = "redqueen-governance-state"

SFN_DIR      = os.path.dirname(os.path.abspath(__file__))
FEEDBACK_DIR = os.path.join(SFN_DIR, "..", "feedback")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load(module_name: str, directory: str):
    for p in list(sys.path):
        if "lambda" in p.replace("\\", "/") and p != directory:
            sys.path.remove(p)
    if directory not in sys.path:
        sys.path.insert(0, directory)
    if module_name in sys.modules:
        return importlib.reload(sys.modules[module_name])
    return importlib.import_module(module_name)


def _create_table(ddb):
    return ddb.create_table(
        TableName=TABLE_NAME,
        KeySchema=[
            {"AttributeName": "resource_id",   "KeyType": "HASH"},
            {"AttributeName": "scan_timestamp", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "resource_id",   "AttributeType": "S"},
            {"AttributeName": "scan_timestamp", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture(autouse=True)
def env():
    env_vars = {
        "AWS_ACCESS_KEY_ID":     "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SECURITY_TOKEN":    "testing",
        "AWS_SESSION_TOKEN":     "testing",
        "AWS_DEFAULT_REGION":    REGION,
        "DYNAMODB_TABLE_NAME":   TABLE_NAME,
        "SNS_TOPIC_ARN":         "",
        "SLACK_WEBHOOK_URL":     "",
        "FEEDBACK_URL":          "https://feedback.example.com",
        "FEEDBACK_SECRET":       "test-secret",
        "DRY_RUN":               "true",
        "AWS_REGION":            REGION,
    }
    with patch.dict(os.environ, env_vars):
        yield
    for mod in ["notify", "check_compliance", "remediate_resource", "handler"]:
        sys.modules.pop(mod, None)


# ═════════════════════════════════════════════════════════════════════════════
# NOTIFY — email + Slack + taskToken
# ═════════════════════════════════════════════════════════════════════════════

@mock_aws
def test_notify_fast_reminder1_stocke_token():
    ddb = boto3.resource("dynamodb", region_name=REGION)
    _create_table(ddb)

    notify = _load("notify", SFN_DIR)
    result = notify.lambda_handler({
        "track": "FAST", "reminder_number": 1, "sla_hours": 168,
        "resource_id": "i-fast-001", "resource_type": "EC2",
        "missing_tags": ["Owner", "Squad"], "criticality": "NON_CRITICAL",
        "task_token": "token-fast-001",
    }, None)

    assert result["notified"] is True
    assert result["reminder_number"] == 1

    items = ddb.Table(TABLE_NAME).query(
        KeyConditionExpression="resource_id = :r",
        ExpressionAttributeValues={":r": "i-fast-001"},
    )["Items"]
    assert any(i.get("task_token") == "token-fast-001" for i in items)


@mock_aws
def test_notify_slow_reminder1_sans_token():
    ddb = boto3.resource("dynamodb", region_name=REGION)
    _create_table(ddb)

    notify = _load("notify", SFN_DIR)
    result = notify.lambda_handler({
        "track": "SLOW", "reminder_number": 1, "sla_hours": 36,
        "resource_id": "db-slow-001", "resource_type": "RDS",
        "missing_tags": ["CostCenter"], "criticality": "CRITICAL",
    }, None)

    assert result["notified"] is True
    items = ddb.Table(TABLE_NAME).query(
        KeyConditionExpression="resource_id = :r",
        ExpressionAttributeValues={":r": "db-slow-001"},
    )["Items"]
    assert not any(i.get("event") == "sfn_wait" for i in items)


@mock_aws
def test_notify_envoie_slack_quand_configure():
    ddb = boto3.resource("dynamodb", region_name=REGION)
    _create_table(ddb)

    with patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://hooks.slack.com/test"}):
        notify = _load("notify", SFN_DIR)
        mock_urlopen = MagicMock()
        mock_urlopen.return_value.__enter__ = MagicMock(return_value=MagicMock(status=200))
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", mock_urlopen):
            result = notify.lambda_handler({
                "track": "SLOW_ESCALATION", "reminder_number": 2, "sla_hours": 168,
                "resource_id": "i-slack-001", "resource_type": "EC2",
                "missing_tags": ["Owner"], "criticality": "CRITICAL",
                "task_token": "token-slack",
            }, None)

    assert "slack" in result["channels"]
    mock_urlopen.assert_called_once()


@mock_aws
def test_notify_slack_absent_ne_crash_pas():
    """Sans SLACK_WEBHOOK_URL, pas de crash, juste email."""
    ddb = boto3.resource("dynamodb", region_name=REGION)
    _create_table(ddb)

    notify = _load("notify", SFN_DIR)
    result = notify.lambda_handler({
        "track": "FAST", "reminder_number": 1, "sla_hours": 168,
        "resource_id": "i-no-slack", "resource_type": "EC2",
        "missing_tags": ["Squad"], "criticality": "NON_CRITICAL",
    }, None)

    assert result["notified"] is True
    assert "slack" not in result["channels"]


@mock_aws
def test_notify_slow_final_reminder_stocke_token():
    ddb = boto3.resource("dynamodb", region_name=REGION)
    _create_table(ddb)

    notify = _load("notify", SFN_DIR)
    result = notify.lambda_handler({
        "track": "SLOW_FINAL_REMINDER", "reminder_number": 3, "sla_hours": 144,
        "resource_id": "db-final-001", "resource_type": "RDS",
        "missing_tags": ["Owner", "Squad"], "criticality": "CRITICAL",
        "task_token": "token-final-001",
    }, None)

    assert result["notified"] is True
    items = ddb.Table(TABLE_NAME).query(
        KeyConditionExpression="resource_id = :r",
        ExpressionAttributeValues={":r": "db-final-001"},
    )["Items"]
    assert any(i.get("task_token") == "token-final-001" for i in items)


# ═════════════════════════════════════════════════════════════════════════════
# CHECK COMPLIANCE
# ═════════════════════════════════════════════════════════════════════════════

@mock_aws
def test_check_conforme_apres_tag():
    ddb = boto3.resource("dynamodb", region_name=REGION)
    _create_table(ddb)
    ddb.Table(TABLE_NAME).put_item(Item={
        "resource_id": "db-ok", "scan_timestamp": "2024-06-01T10:00:00",
        "resource_type": "RDS", "event": "feedback",
        "feedback_action": "tag", "compliant": True, "ttl_expiry": 9999999999,
    })

    check  = _load("check_compliance", SFN_DIR)
    result = check.lambda_handler({"resource_id": "db-ok", "resource_type": "RDS"}, None)
    assert result["compliant"] is True


@mock_aws
def test_check_non_conforme():
    ddb = boto3.resource("dynamodb", region_name=REGION)
    _create_table(ddb)
    ddb.Table(TABLE_NAME).put_item(Item={
        "resource_id": "db-nok", "scan_timestamp": "2024-06-01T10:00:00",
        "resource_type": "RDS", "event": "scan",
        "compliant": False, "ttl_expiry": 9999999999,
    })

    check  = _load("check_compliance", SFN_DIR)
    result = check.lambda_handler({"resource_id": "db-nok", "resource_type": "RDS"}, None)
    assert result["compliant"] is False


@mock_aws
def test_check_not_found():
    ddb = boto3.resource("dynamodb", region_name=REGION)
    _create_table(ddb)

    check  = _load("check_compliance", SFN_DIR)
    result = check.lambda_handler({"resource_id": "i-ghost", "resource_type": "EC2"}, None)
    assert result["compliant"] is False
    assert result["source"] == "not_found"


# ═════════════════════════════════════════════════════════════════════════════
# REMEDIATE RESOURCE
# ═════════════════════════════════════════════════════════════════════════════

@mock_aws
def test_remediate_dry_run_ne_touche_pas_aws():
    ec2  = boto3.client("ec2", region_name=REGION)
    resp = ec2.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)
    iid  = resp["Instances"][0]["InstanceId"]

    rem    = _load("remediate_resource", SFN_DIR)
    result = rem.lambda_handler({
        "resource_id": iid, "resource_type": "EC2",
        "reason": "sla_expired_non_critical", "dry_run": True,
    }, None)

    assert result["remediated"] is False
    assert result["dry_run"] is True
    state = ec2.describe_instances(InstanceIds=[iid])
    assert state["Reservations"][0]["Instances"][0]["State"]["Name"] == "running"


@mock_aws
def test_remediate_ec2_stop_et_quarantine():
    ec2  = boto3.client("ec2", region_name=REGION)
    resp = ec2.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)
    iid  = resp["Instances"][0]["InstanceId"]

    with patch.dict(os.environ, {"DRY_RUN": "false"}):
        rem    = _load("remediate_resource", SFN_DIR)
        result = rem.lambda_handler({
            "resource_id": iid, "resource_type": "EC2",
            "reason": "sla_expired_non_critical", "dry_run": False,
        }, None)

    assert result["remediated"] is True
    assert result["action"] == "stopped_and_quarantined"

    state = ec2.describe_instances(InstanceIds=[iid])
    inst  = state["Reservations"][0]["Instances"][0]
    assert inst["State"]["Name"] in ("stopping", "stopped")
    tag_map = {t["Key"]: t["Value"] for t in inst.get("Tags", [])}
    assert tag_map.get("Status") == "Quarantined"


@mock_aws
def test_remediate_s3_block_public_et_quarantine():
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(
        Bucket="bucket-noncompliant",
        CreateBucketConfiguration={"LocationConstraint": REGION}
    )

    with patch.dict(os.environ, {"DRY_RUN": "false"}):
        rem    = _load("remediate_resource", SFN_DIR)
        result = rem.lambda_handler({
            "resource_id": "bucket-noncompliant", "resource_type": "S3",
            "reason": "sla_expired_non_critical", "dry_run": False,
        }, None)

    assert result["remediated"] is True
    assert result["action"] == "public_access_blocked_and_quarantined"

    pab = s3.get_public_access_block(Bucket="bucket-noncompliant")
    cfg = pab["PublicAccessBlockConfiguration"]
    assert cfg["BlockPublicAcls"] is True
    assert cfg["BlockPublicPolicy"] is True

    tags = s3.get_bucket_tagging(Bucket="bucket-noncompliant")
    tag_map = {t["Key"]: t["Value"] for t in tags["TagSet"]}
    assert tag_map.get("Status") == "Quarantined"


@mock_aws
def test_remediate_lambda_throttle_et_quarantine():
    iam = boto3.client("iam", region_name=REGION)
    lam = boto3.client("lambda", region_name=REGION)

    iam.create_role(
        RoleName="test-role",
        AssumeRolePolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{"Effect": "Allow",
                           "Principal": {"Service": "lambda.amazonaws.com"},
                           "Action": "sts:AssumeRole"}]
        }),
        Path="/"
    )
    func = lam.create_function(
        FunctionName="func-noncompliant",
        Runtime="python3.12",
        Role=f"arn:aws:iam::{ACCOUNT_ID}:role/test-role",
        Handler="index.handler",
        Code={"ZipFile": b"fake"},
    )

    with patch.dict(os.environ, {"DRY_RUN": "false"}):
        rem    = _load("remediate_resource", SFN_DIR)
        result = rem.lambda_handler({
            "resource_id": "func-noncompliant", "resource_type": "LAMBDA",
            "reason": "sla_expired_non_critical", "dry_run": False,
        }, None)

    assert result["remediated"] is True
    assert result["action"] == "throttled_and_quarantined"

    concurrency = lam.get_function_concurrency(FunctionName="func-noncompliant")
    assert concurrency.get("ReservedConcurrentExecutions") == 0


@mock_aws
def test_remediate_persiste_dans_dynamodb():
    ddb = boto3.resource("dynamodb", region_name=REGION)
    _create_table(ddb)
    ec2 = boto3.client("ec2", region_name=REGION)
    resp = ec2.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)
    iid  = resp["Instances"][0]["InstanceId"]

    with patch.dict(os.environ, {"DRY_RUN": "false"}):
        rem = _load("remediate_resource", SFN_DIR)
        rem.lambda_handler({
            "resource_id": iid, "resource_type": "EC2",
            "reason": "sla_expired_non_critical", "dry_run": False,
        }, None)

    items = ddb.Table(TABLE_NAME).query(
        KeyConditionExpression="resource_id = :r",
        ExpressionAttributeValues={":r": iid},
    )["Items"]
    assert any(i.get("event") == "remediation" for i in items)


# ═════════════════════════════════════════════════════════════════════════════
# FLUX COMPLETS END-TO-END
# ═════════════════════════════════════════════════════════════════════════════

@mock_aws
def test_fast_flux_approve_handled():
    """FAST: notify → owner approuve → SendTaskSuccess(approve) → Handled."""
    ddb = boto3.resource("dynamodb", region_name=REGION)
    _create_table(ddb)

    notify = _load("notify", SFN_DIR)
    notify.lambda_handler({
        "track": "FAST", "reminder_number": 1, "sla_hours": 168,
        "resource_id": "i-e2e-approve", "resource_type": "EC2",
        "missing_tags": ["Owner"], "criticality": "NON_CRITICAL",
        "task_token": "token-e2e-approve",
    }, None)

    feedback = _load("handler", FEEDBACK_DIR)
    mock_sfn = MagicMock()
    feedback.sfn_client = mock_sfn

    import hmac as _h, hashlib as _hs
    tok = _h.new(b"test-secret", b"i-e2e-approve:approve", _hs.sha256).hexdigest()

    result = feedback.lambda_handler({
        "queryStringParameters": {
            "resource_id": "i-e2e-approve", "resource_type": "EC2",
            "action": "approve", "token": tok,
        }
    }, None)

    assert result["statusCode"] == 200
    output = json.loads(mock_sfn.send_task_success.call_args[1]["output"])
    assert output["feedback_action"] == "approve"
    # ASL route vers FAST_Handled → Succeed


@mock_aws
def test_fast_flux_reject_vers_remediate():
    """FAST: notify → owner rejette → SendTaskSuccess(reject) → RemediateResource."""
    ddb = boto3.resource("dynamodb", region_name=REGION)
    _create_table(ddb)

    notify = _load("notify", SFN_DIR)
    notify.lambda_handler({
        "track": "FAST", "reminder_number": 1, "sla_hours": 168,
        "resource_id": "i-e2e-reject", "resource_type": "EC2",
        "missing_tags": ["Squad"], "criticality": "NON_CRITICAL",
        "task_token": "token-e2e-reject",
    }, None)

    feedback = _load("handler", FEEDBACK_DIR)
    mock_sfn = MagicMock()
    feedback.sfn_client = mock_sfn

    import hmac as _h, hashlib as _hs
    tok = _h.new(b"test-secret", b"i-e2e-reject:reject", _hs.sha256).hexdigest()

    feedback.lambda_handler({
        "queryStringParameters": {
            "resource_id": "i-e2e-reject", "resource_type": "EC2",
            "action": "reject", "token": tok,
        }
    }, None)

    output = json.loads(mock_sfn.send_task_success.call_args[1]["output"])
    assert output["feedback_action"] == "reject"
    # ASL route vers FAST_RemediateResource (stop + quarantine)


@mock_aws
def test_slow_flux_conforme_apres_36h():
    """SLOW: notify → 36h → check → compliant=True → NowCompliant."""
    ddb = boto3.resource("dynamodb", region_name=REGION)
    _create_table(ddb)

    # Simuler que le propriétaire a taggé pendant les 36h
    ddb.Table(TABLE_NAME).put_item(Item={
        "resource_id": "db-e2e-slow-ok", "scan_timestamp": "2024-06-01T12:00:00",
        "resource_type": "RDS", "event": "feedback",
        "feedback_action": "tag", "compliant": True, "ttl_expiry": 9999999999,
    })

    check  = _load("check_compliance", SFN_DIR)
    result = check.lambda_handler(
        {"resource_id": "db-e2e-slow-ok", "resource_type": "RDS"}, None
    )
    assert result["compliant"] is True
    # ASL route vers SLOW_NowCompliant → Succeed


@mock_aws
def test_slow_flux_escalation_puis_remediate():
    """SLOW: check non-conforme → escalation → timeout → RemediateResource."""
    ddb = boto3.resource("dynamodb", region_name=REGION)
    _create_table(ddb)
    ec2 = boto3.client("ec2", region_name=REGION)
    resp = ec2.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)
    iid  = resp["Instances"][0]["InstanceId"]

    # check retourne non-conforme
    ddb.Table(TABLE_NAME).put_item(Item={
        "resource_id": iid, "scan_timestamp": "2024-06-01T10:00:00",
        "resource_type": "EC2", "event": "scan",
        "compliant": False, "ttl_expiry": 9999999999,
    })

    check  = _load("check_compliance", SFN_DIR)
    result = check.lambda_handler({"resource_id": iid, "resource_type": "EC2"}, None)
    assert result["compliant"] is False

    # Timeout → remediate
    with patch.dict(os.environ, {"DRY_RUN": "false"}):
        rem    = _load("remediate_resource", SFN_DIR)
        result = rem.lambda_handler({
            "resource_id": iid, "resource_type": "EC2",
            "reason": "sla_expired_critical", "dry_run": False,
        }, None)

    assert result["remediated"] is True
    assert result["action"] == "stopped_and_quarantined"


@mock_aws
def test_slow_flux_escalation_puis_approve():
    """SLOW: escalation → owner approuve → SendTaskSuccess → SLOW_Handled."""
    ddb = boto3.resource("dynamodb", region_name=REGION)
    _create_table(ddb)

    notify = _load("notify", SFN_DIR)
    notify.lambda_handler({
        "track": "SLOW_ESCALATION", "reminder_number": 2, "sla_hours": 168,
        "resource_id": "db-e2e-esc", "resource_type": "RDS",
        "missing_tags": ["Owner"], "criticality": "CRITICAL",
        "task_token": "token-esc-001",
    }, None)

    feedback = _load("handler", FEEDBACK_DIR)
    mock_sfn = MagicMock()
    feedback.sfn_client = mock_sfn

    import hmac as _h, hashlib as _hs
    tok = _h.new(b"test-secret", b"db-e2e-esc:approve", _hs.sha256).hexdigest()

    result = feedback.lambda_handler({
        "queryStringParameters": {
            "resource_id": "db-e2e-esc", "resource_type": "RDS",
            "action": "approve", "token": tok,
        }
    }, None)

    assert result["statusCode"] == 200
    output = json.loads(mock_sfn.send_task_success.call_args[1]["output"])
    assert output["feedback_action"] == "approve"
    # ASL route vers SLOW_Handled → Succeed
