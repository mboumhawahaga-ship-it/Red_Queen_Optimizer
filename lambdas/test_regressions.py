"""Integration regressions: real shared functions, moto AWS, no external network."""
import importlib.util
import json
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

ROOT = Path(__file__).parent


@pytest.fixture
def platform(monkeypatch):
    """Use an isolated package: legacy tests stub the normal package globally."""
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("GOVERNANCE_TABLE", "governance")
    monkeypatch.setenv("DEFAULT_OWNER", "")
    monkeypatch.setenv("DEFAULT_SQUAD", "")
    monkeypatch.setenv("DEFAULT_COST_CENTER", "")
    monkeypatch.setenv("SNS_TOPIC_ARN", "")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "")
    monkeypatch.setenv("SCHEDULER_ROLE_ARN", "arn:aws:iam::123456789012:role/scheduler")
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_ARN", "arn:aws:lambda:eu-west-1:123456789012:function:evaluator")
    loaded = {}
    for package in ("rq_regression", "rq_regression.shared", "rq_regression.auto_tagger",
                    "rq_regression.evaluator", "rq_regression.feedback"):
        module = types.ModuleType(package)
        module.__path__ = []
        monkeypatch.setitem(sys.modules, package, module)
    with mock_aws():
        boto3.resource("dynamodb").create_table(
            TableName="governance", BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "resource_id", "KeyType": "HASH"},
                       {"AttributeName": "scan_timestamp", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": name, "AttributeType": "S"}
                                  for name in ("resource_id", "scan_timestamp")],
        )
        for name, path in (
            ("shared.constants", "shared/constants.py"), ("shared.utils", "shared/utils.py"),
            ("auto_tagger.handler", "auto-tagger/handler.py"),
            ("evaluator.handler", "compliance-evaluator/handler.py"),
            ("feedback.handler", "feedback-api/handler.py"),
        ):
            spec = importlib.util.spec_from_file_location(f"rq_regression.{name}", ROOT / path)
            module = importlib.util.module_from_spec(spec)
            monkeypatch.setitem(sys.modules, spec.name, module)
            spec.loader.exec_module(module)
            loaded[name] = module
        yield types.SimpleNamespace(
            utils=loaded["shared.utils"], tagger=loaded["auto_tagger.handler"],
            evaluator=loaded["evaluator.handler"], feedback=loaded["feedback.handler"],
        )


@pytest.mark.parametrize("value", [None, "", "   ", "Unknown", " CC-000 ", "n/a", "TBD",
                                         "unassigned", "auto-tagger@example.com", 42, False])
def test_filler_values_are_missing(platform, value):
    tags = [{"Key": key, "Value": "valid"} for key in ("Owner", "Squad", "CostCenter", "Environment")]
    tags[0]["Value"] = value
    assert platform.utils.check_required_tags(tags) == (False, ["Owner"])


@pytest.mark.parametrize("value", ["platform", "CC-42", "CC-0001", "alice@example.com", "prod"])
def test_real_values_are_accepted(platform, value):
    assert not platform.utils.is_placeholder_value(value)


def test_request_filler_falls_back_to_creator(platform):
    defaults = platform.tagger._build_defaults({
        "requestParameters": {"tags": {"Owner": "unknown"}},
        "userIdentity": {"arn": "arn:aws:iam::123456789012:user/alice@example.com"},
    }, "fn", "lambda")
    assert defaults["Owner"] == "alice@example.com"


@pytest.mark.parametrize("dry_run", [False, True])
def test_s3_preserves_tags_and_reports_actual_state(platform, dry_run):
    h = platform.tagger
    h.DRY_RUN = dry_run
    h.DEFAULT_OWNER = "alice@example.com"
    s3 = boto3.client("s3")
    s3.create_bucket(Bucket="rq-existing", CreateBucketConfiguration={"LocationConstraint": "eu-west-1"})
    original = {"Project": "apollo", "Environment": "prod"}
    s3.put_bucket_tagging(Bucket="rq-existing", Tagging={"TagSet": [
        {"Key": k, "Value": v} for k, v in original.items()
    ]})
    response = h.lambda_handler({"detail": {
        "eventName": "CreateBucket", "requestParameters": {"bucketName": "rq-existing"},
    }}, None)
    result = json.loads(response["body"])["s3"][0]
    actual = {t["Key"]: t["Value"] for t in s3.get_bucket_tagging(Bucket="rq-existing")["TagSet"]}
    assert actual == (original if dry_run else {**original, "Owner": "alice@example.com"})
    assert result["tags_added"] == ([] if dry_run else ["Owner"])
    expected_missing = {"Owner", "Squad", "CostCenter"} if dry_run else {"Squad", "CostCenter"}
    assert set(result["tags_unresolved"]) == expected_missing
    records = boto3.resource("dynamodb").Table("governance").scan()["Items"]
    assert len(records) == (0 if dry_run else 1)


def test_no_defaults_does_not_claim_compliance(platform):
    result = platform.tagger._apply_tags("i-123", "EC2", {}, {}, Mock())
    assert result["status"] == "needs_owner"
    record = platform.utils.get_governance_record("governance", "i-123")
    assert record["compliant"] is False
    assert len(record["missing_tags"]) == 4


@pytest.mark.parametrize("event_name", [
    "RunInstances", "CreateBucket", "CreateDBInstance", "CreateFunction20150331",
])
def test_null_cloudtrail_sections_are_safe(platform, event_name):
    result = platform.tagger.lambda_handler({"detail": {
        "eventName": event_name, "requestParameters": None, "responseElements": None,
    }}, None)
    assert result["statusCode"] == 200


def test_dry_run_has_no_persistence_notification_scheduler_or_metric(platform, monkeypatch):
    h = platform.evaluator
    h.DRY_RUN = True
    forbidden = Mock(side_effect=AssertionError("write attempted"))
    monkeypatch.setattr(h, "send_sns", forbidden)
    monkeypatch.setattr(h, "send_slack", forbidden)
    monkeypatch.setattr(h, "_schedule_sla_check", forbidden)
    monkeypatch.setattr(platform.utils.boto3, "client", forbidden)
    result = h.lambda_handler({"detail": {"configurationItem": {
        "resourceId": "i-123", "resourceType": "AWS::EC2::Instance", "tags": {},
    }}}, None)
    assert result["status"] == "alerted"
    forbidden.assert_not_called()
    assert boto3.resource("dynamodb").Table("governance").scan()["Count"] == 0


def test_real_config_shape_fetches_live_tags(platform, monkeypatch):
    fetch = Mock(return_value=[{"Key": "Owner", "Value": "alice@example.com"}])
    monkeypatch.setattr(platform.evaluator, "_fetch_current_tags", fetch)
    result = platform.evaluator._extract_event_fields({"region": "eu-west-1", "detail": {
        "newEvaluationResult": {"evaluationResultIdentifier": {"evaluationResultQualifier": {
            "resourceId": "i-123", "resourceType": "AWS::EC2::Instance",
        }}},
    }})
    assert result[:2] == ("i-123", "ec2")
    assert result[2] == fetch.return_value
    fetch.assert_called_once_with("i-123", "ec2")


def test_repeat_event_keeps_original_sla_and_does_not_notify(platform, monkeypatch):
    h = platform.evaluator
    deadline = (datetime.now(timezone.utc) + timedelta(hours=36)).isoformat()
    platform.utils.put_governance_record("governance", "i-123", "ec2", "alerted", dry_run=False,
                                         extra={"status": "alerted", "sla_deadline": deadline})
    notify = Mock()
    monkeypatch.setattr(h, "_send_alert", notify)
    result = h.lambda_handler({"detail": {"configurationItem": {
        "resourceId": "i-123", "resourceType": "AWS::EC2::Instance", "tags": {},
    }}}, None)
    assert result["reason"] == "already_alerted"
    assert result["sla_deadline"] == deadline
    notify.assert_not_called()


def test_approved_resource_is_not_quarantined_even_after_auto_tag_record(platform, monkeypatch):
    platform.utils.put_governance_record("governance", "i-123", "ec2", "feedback", dry_run=False,
                                         extra={"status": "handled"})
    platform.utils.put_governance_record("governance", "i-123", "ec2", "auto_tag", dry_run=False)
    quarantine = Mock()
    monkeypatch.setattr(platform.evaluator, "_apply_quarantine_tags", quarantine)
    result = platform.evaluator.lambda_handler({"detail-type": "SLADeadline", "detail": {
        "resource_id": "i-123", "resource_type": "ec2",
    }}, None)
    assert result["reason"] == "handled"
    quarantine.assert_not_called()


def test_feedback_query_paginates_past_twenty_newer_records(platform):
    platform.utils.put_governance_record("governance", "i-123", "ec2", "feedback", dry_run=False,
                                         extra={"status": "handled"})
    for _ in range(25):
        platform.utils.put_governance_record("governance", "i-123", "ec2", "auto_tag", dry_run=False)
    assert platform.utils.get_governance_record("governance", "i-123", "feedback")["status"] == "handled"


@pytest.mark.parametrize("body", ["[]", "true", '"text"', "42", "null"])
def test_non_object_feedback_json_returns_400(platform, body):
    response = platform.feedback.lambda_handler({"httpMethod": "POST", "body": body}, None)
    assert response["statusCode"] == 400


def test_failed_quarantine_does_not_record_success(platform, monkeypatch):
    h = platform.evaluator
    monkeypatch.setattr(h, "_fetch_current_tags", Mock(return_value=[]))
    failure = ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "CreateTags")
    monkeypatch.setattr(h, "_apply_quarantine_tags", Mock(side_effect=failure))
    with pytest.raises(ClientError):
        h.lambda_handler({"detail-type": "SLADeadline", "detail": {
            "resource_id": "i-123", "resource_type": "ec2",
        }}, None)
    assert platform.utils.get_governance_record("governance", "i-123") is None


def test_schedule_names_respect_length_and_avoid_sanitization_collisions(platform):
    h = platform.evaluator
    assert len(h._schedule_name("x" * 512)) <= 64
    assert h._schedule_name("a:b") != h._schedule_name("a/b")


def test_scheduler_retry_reuses_original_deadline(platform):
    h = platform.evaluator
    first = (datetime.now(timezone.utc) + timedelta(hours=36)).isoformat()
    later = (datetime.now(timezone.utc) + timedelta(hours=37)).isoformat()
    assert h._schedule_sla_check("i-123", "ec2", "CRITICAL", first, "eu-west-1") == first
    assert h._schedule_sla_check("i-123", "ec2", "CRITICAL", later, "eu-west-1") == first
    scheduled = boto3.client("scheduler").get_schedule(Name=h._schedule_name("i-123"))
    actual_time = datetime.fromisoformat(scheduled["ScheduleExpression"][3:-1]).replace(tzinfo=timezone.utc)
    assert actual_time >= datetime.fromisoformat(first)


def test_missing_scheduler_configuration_fails_visibly(platform):
    platform.evaluator.SCHEDULER_ROLE = ""
    with pytest.raises(RuntimeError, match="required"):
        platform.evaluator._schedule_sla_check("i-123", "ec2", "CRITICAL", "2030-01-01T00:00:00Z", "eu-west-1")


def test_notification_failure_is_not_reported_as_success(platform, monkeypatch):
    h = platform.evaluator
    h.SNS_TOPIC_ARN = "arn:aws:sns:eu-west-1:123456789012:alerts"
    monkeypatch.setattr(h, "send_sns", Mock(return_value=False))
    monkeypatch.setattr(h, "send_slack", Mock(return_value=True))
    with pytest.raises(RuntimeError, match="notification"):
        h._send_alert("i-123", "ec2", "CRITICAL", ["Owner"], 36,
                      "2030-01-01T00:00:00Z", "123456789012", "eu-west-1")


def test_aws_tag_read_failure_is_not_interpreted_as_missing_tags(platform, monkeypatch):
    failure = ClientError({"Error": {"Code": "AccessDenied", "Message": "denied"}}, "DescribeTags")
    monkeypatch.setattr(platform.evaluator.boto3, "client", Mock(side_effect=failure))
    with pytest.raises(ClientError):
        platform.evaluator._fetch_current_tags("i-123", "ec2")


@pytest.mark.parametrize("service", ["ec2", "rds", "lambda"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_auto_tagging_other_services_with_real_shared_helpers(platform, service, dry_run):
    h = platform.tagger
    h.DRY_RUN = dry_run
    h.DEFAULT_OWNER = "alice@example.com"
    h.DEFAULT_SQUAD = "platform"
    h.DEFAULT_COST_CENTER = "CC-42"
    if service == "ec2":
        instance = boto3.client("ec2").run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)
        rid = instance["Instances"][0]["InstanceId"]
        detail = {"eventName": "RunInstances", "responseElements": {
            "instancesSet": {"items": [{"instanceId": rid}]},
        }}

        def read():
            return h._get_ec2_tags(rid)
    elif service == "rds":
        db = boto3.client("rds").create_db_instance(
            DBInstanceIdentifier="rq-test-db", DBInstanceClass="db.t3.micro", Engine="postgres",
            MasterUsername="testuser", MasterUserPassword="fictitious-test-password", AllocatedStorage=20,
        )["DBInstance"]
        rid = db["DBInstanceArn"]
        detail = {"eventName": "CreateDBInstance", "requestParameters": {
            "dBInstanceIdentifier": "rq-test-db",
        }}

        def read():
            return h._get_rds_tags(rid)
    else:
        import io
        import zipfile
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("handler.py", "def handler(event, context): return event")
        role = boto3.client("iam").create_role(
            RoleName="test-lambda", AssumeRolePolicyDocument=json.dumps({
                "Version": "2012-10-17", "Statement": [{
                    "Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }],
            }),
        )["Role"]["Arn"]
        rid = boto3.client("lambda").create_function(
            FunctionName="rq-test-function", Runtime="python3.12", Role=role,
            Handler="handler.handler", Code={"ZipFile": archive.getvalue()},
        )["FunctionArn"]
        # No ARN in the CloudTrail response: resolve it instead of claiming a no-op succeeded.
        detail = {"eventName": "CreateFunction20150331", "requestParameters": {
            "functionName": "rq-test-function",
        }, "responseElements": None}

        def read():
            return h._get_lambda_tags(rid)
    result = json.loads(h.lambda_handler({"detail": detail}, None)["body"])[service][0]
    assert result["status"] == ("dry_run" if dry_run else "tagged")
    assert len(result["tags_added"]) == (0 if dry_run else 4)
    assert read() == ({} if dry_run else {
        "Owner": "alice@example.com", "Squad": "platform", "CostCenter": "CC-42", "Environment": "dev",
    })


def test_failed_tagging_is_retried_instead_of_persisting_success(platform):
    failed_write = Mock(side_effect=RuntimeError("tag write failed"))
    with pytest.raises(RuntimeError, match="tag write failed"):
        platform.tagger._apply_tags("i-123", "EC2", {}, {"Owner": "alice@example.com"}, failed_write)
    assert platform.utils.get_governance_record("governance", "i-123") is None


def test_feedback_dry_run_does_not_create_an_exemption(platform):
    platform.feedback.DRY_RUN = True
    response = platform.feedback.lambda_handler({"httpMethod": "POST", "body": json.dumps({
        "resource_id": "i-123", "action": "approve",
    })}, None)
    assert json.loads(response["body"])["dry_run"] is True
    assert platform.utils.get_governance_record("governance", "i-123") is None


@pytest.mark.parametrize("resource_type,resource_id,key,arn", [
    ("ec2", "i-123", "ec2_instance_arns", "arn:aws:ec2:eu-west-1:123456789012:instance/i-123"),
    ("s3", "rq-demo", "s3_bucket_arns", "arn:aws:s3:::rq-demo"),
    ("rds", "demo-db", "rds_db_arns", "arn:aws:rds:eu-west-1:123456789012:db:demo-db"),
    ("lambda", "demo-fn", "lambda_function_arns", "arn:aws:lambda:eu-west-1:123456789012:function:demo-fn"),
])
def test_scope_accepts_only_listed_arns(platform, monkeypatch, resource_type, resource_id, key, arn):
    monkeypatch.setenv("GOVERNANCE_ACCOUNT_ID", "123456789012")
    monkeypatch.setenv("GOVERNANCE_REGION", "eu-west-1")
    monkeypatch.setenv("GOVERNANCE_SCOPE", json.dumps({key: [arn]}))
    assert platform.utils.resource_in_governance_scope(resource_id, resource_type)
    assert platform.utils.resource_in_governance_scope(arn, resource_type)
    assert not platform.utils.resource_in_governance_scope(resource_id + "-other", resource_type)
    monkeypatch.setenv("GOVERNANCE_SCOPE", "{}")
    assert not platform.utils.resource_in_governance_scope(resource_id, resource_type)


@pytest.mark.parametrize("detail", [
    {"eventName": "RunInstances", "responseElements": {"instancesSet": {"items": [{"instanceId": "i-123"}]}}},
    {"eventName": "CreateBucket", "requestParameters": {"bucketName": "rq-demo"}},
    {"eventName": "CreateDBInstance", "requestParameters": {"dBInstanceIdentifier": "demo-db"}},
    {"eventName": "CreateFunction20150331", "requestParameters": {"functionName": "demo-fn"}},
])
def test_auto_tagger_does_not_read_or_write_unlisted_workloads(platform, monkeypatch, detail):
    monkeypatch.setenv("GOVERNANCE_SCOPE", "{}")
    forbidden = Mock(side_effect=AssertionError("out-of-scope AWS call"))
    for name in ("ec2_client", "s3_client", "rds_client", "lambda_client"):
        monkeypatch.setattr(platform.tagger, name, forbidden)
    platform.tagger.lambda_handler({"detail": detail}, None)
    assert forbidden.mock_calls == []


def test_unlisted_config_event_is_skipped_before_reading_tags(platform, monkeypatch):
    monkeypatch.setenv("GOVERNANCE_SCOPE", "{}")
    fetch = Mock(side_effect=AssertionError("out-of-scope read"))
    monkeypatch.setattr(platform.evaluator, "_fetch_current_tags", fetch)
    result = platform.evaluator.lambda_handler({"detail": {
        "newEvaluationResult": {"evaluationResultIdentifier": {"evaluationResultQualifier": {
            "resourceType": "AWS::EC2::Instance", "resourceId": "i-123",
        }}},
    }}, None)
    assert result == {"status": "skipped", "reason": "out_of_scope"}
    fetch.assert_not_called()


def test_scheduler_uses_and_deletes_only_configured_group(platform):
    h = platform.evaluator
    h.SCHEDULER_GROUP = "red-queen-dev-sla"
    scheduler = boto3.client("scheduler")
    scheduler.create_schedule_group(Name=h.SCHEDULER_GROUP)
    deadline = (datetime.now(timezone.utc) + timedelta(hours=36)).isoformat()
    h._schedule_sla_check("i-123", "ec2", "CRITICAL", deadline, "eu-west-1")
    created = scheduler.get_schedule(Name=h._schedule_name("i-123"), GroupName=h.SCHEDULER_GROUP)
    assert created["GroupName"] == h.SCHEDULER_GROUP
    h._delete_schedule(h._schedule_name("i-123"))
    assert scheduler.list_schedules(GroupName=h.SCHEDULER_GROUP)["Schedules"] == []
