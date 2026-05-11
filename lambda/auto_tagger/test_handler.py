"""
Tests unitaires pour lambda/auto_tagger/handler.py

Couvre :
- DRY_RUN=true  : aucune modification AWS, status=dry_run
- DRY_RUN=false : tags appliqués sur EC2 / S3 / RDS / Lambda
- Idempotence   : ressource déjà conforme → status=already_compliant
- Déduction Owner depuis l'identité IAM de l'appelant
- Événement inconnu → ignoré proprement
"""

import os
import sys
import json
import importlib
import pytest
import boto3
from unittest.mock import patch
from moto import mock_aws

REGION      = "eu-west-1"
ACCOUNT_ID  = "123456789012"
HANDLER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_handler():
    if HANDLER_DIR not in sys.path:
        sys.path.insert(0, HANDLER_DIR)
    if "handler" in sys.modules:
        return importlib.reload(sys.modules["handler"])
    import handler
    return handler


def _event(event_name: str, detail_extra: dict = None) -> dict:
    """Construit un événement EventBridge minimal."""
    detail = {
        "eventName": event_name,
        "awsRegion": REGION,
        "userIdentity": {
            "accountId": ACCOUNT_ID,
            "arn": f"arn:aws:iam::{ACCOUNT_ID}:user/jean.dupont@entreprise.com",
            "principalId": f"AIDAI:{ACCOUNT_ID}",
        },
        "requestParameters": {},
        "responseElements": {},
    }
    if detail_extra:
        for k, v in detail_extra.items():
            if isinstance(v, dict) and isinstance(detail.get(k), dict):
                detail[k].update(v)
            else:
                detail[k] = v
    return {"detail": detail}


@pytest.fixture(autouse=True)
def aws_env():
    env_vars = {
        "AWS_ACCESS_KEY_ID":     "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SECURITY_TOKEN":    "testing",
        "AWS_SESSION_TOKEN":     "testing",
        "AWS_DEFAULT_REGION":    REGION,
        "DRY_RUN":               "true",
        "DYNAMODB_TABLE_NAME":   "",
        "DEFAULT_ENVIRONMENT":   "dev",
        "DEFAULT_SQUAD":         "Platform",
        "DEFAULT_COST_CENTER":   "CC-000",
        "DEFAULT_OWNER":         "auto-tagger@entreprise.com",
    }
    with patch.dict(os.environ, env_vars):
        yield
    if "handler" in sys.modules:
        del sys.modules["handler"]


# ── Événement inconnu ─────────────────────────────────────────────────────────

def test_evenement_inconnu_est_ignore():
    handler = load_handler()
    result  = handler.lambda_handler({"detail": {"eventName": "DescribeInstances"}}, None)
    body    = json.loads(result["body"])
    assert body["skipped"] == "DescribeInstances"


# ── EC2 ───────────────────────────────────────────────────────────────────────

@mock_aws
def test_ec2_dry_run_ne_modifie_pas_les_tags():
    ec2  = boto3.client("ec2", region_name=REGION)
    resp = ec2.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)
    iid  = resp["Instances"][0]["InstanceId"]

    event = _event("RunInstances", {
        "responseElements": {
            "instancesSet": {"items": [{"instanceId": iid}]}
        }
    })

    handler = load_handler()
    result  = handler.lambda_handler(event, None)
    body    = json.loads(result["body"])

    assert body["ec2"][0]["status"] == "dry_run"
    assert "Owner" in body["ec2"][0]["tags_added"]

    # Aucun tag ne doit avoir été posé sur l'instance
    tags = ec2.describe_instances(InstanceIds=[iid])
    raw  = tags["Reservations"][0]["Instances"][0].get("Tags") or []
    assert raw == []


@mock_aws
def test_ec2_applique_tags_manquants_en_mode_reel():
    ec2  = boto3.client("ec2", region_name=REGION)
    resp = ec2.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)
    iid  = resp["Instances"][0]["InstanceId"]

    event = _event("RunInstances", {
        "responseElements": {
            "instancesSet": {"items": [{"instanceId": iid}]}
        }
    })

    with patch.dict(os.environ, {"DRY_RUN": "false"}):
        handler = load_handler()
        result  = handler.lambda_handler(event, None)
        body    = json.loads(result["body"])

    assert body["ec2"][0]["status"] == "tagged"
    assert set(body["ec2"][0]["tags_added"]) == {"Owner", "Squad", "CostCenter", "Environment"}

    # Les tags doivent être présents sur l'instance
    tags_resp = ec2.describe_instances(InstanceIds=[iid])
    tag_keys  = {t["Key"] for t in tags_resp["Reservations"][0]["Instances"][0].get("Tags", [])}
    assert {"Owner", "Squad", "CostCenter", "Environment"}.issubset(tag_keys)


@mock_aws
def test_ec2_deja_conforme_est_idempotent():
    ec2  = boto3.client("ec2", region_name=REGION)
    resp = ec2.run_instances(
        ImageId="ami-12345678", MinCount=1, MaxCount=1,
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Owner",       "Value": "jean@entreprise.com"},
            {"Key": "Squad",       "Value": "Data"},
            {"Key": "CostCenter",  "Value": "CC-123"},
            {"Key": "Environment", "Value": "dev"},
        ]}]
    )
    iid = resp["Instances"][0]["InstanceId"]

    event = _event("RunInstances", {
        "responseElements": {
            "instancesSet": {"items": [{"instanceId": iid}]}
        }
    })

    with patch.dict(os.environ, {"DRY_RUN": "false"}):
        handler = load_handler()
        result  = handler.lambda_handler(event, None)
        body    = json.loads(result["body"])

    assert body["ec2"][0]["status"] == "already_compliant"
    assert body["ec2"][0]["tags_added"] == []


@mock_aws
def test_ec2_owner_deduit_depuis_iam_user():
    ec2  = boto3.client("ec2", region_name=REGION)
    resp = ec2.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)
    iid  = resp["Instances"][0]["InstanceId"]

    event = _event("RunInstances", {
        "responseElements": {
            "instancesSet": {"items": [{"instanceId": iid}]}
        },
        "userIdentity": {
            "arn": f"arn:aws:iam::{ACCOUNT_ID}:user/marie.martin@entreprise.com"
        }
    })

    with patch.dict(os.environ, {"DRY_RUN": "false"}):
        handler = load_handler()
        handler.lambda_handler(event, None)

    tags_resp = ec2.describe_instances(InstanceIds=[iid])
    tag_map   = {t["Key"]: t["Value"]
                 for t in tags_resp["Reservations"][0]["Instances"][0].get("Tags", [])}
    assert tag_map.get("Owner") == "marie.martin@entreprise.com"


# ── S3 ────────────────────────────────────────────────────────────────────────

@mock_aws
def test_s3_dry_run_ne_modifie_pas_les_tags():
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(
        Bucket="mon-bucket-test",
        CreateBucketConfiguration={"LocationConstraint": REGION}
    )

    event = _event("CreateBucket", {
        "requestParameters": {"bucketName": "mon-bucket-test"}
    })

    handler = load_handler()
    result  = handler.lambda_handler(event, None)
    body    = json.loads(result["body"])

    assert body["s3"][0]["status"] == "dry_run"

    # Aucun tag posé
    try:
        s3.get_bucket_tagging(Bucket="mon-bucket-test")
        assert False, "Ne devrait pas avoir de tags"
    except s3.exceptions.ClientError:
        pass


@mock_aws
def test_s3_applique_tags_en_mode_reel():
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(
        Bucket="bucket-sans-tags",
        CreateBucketConfiguration={"LocationConstraint": REGION}
    )

    event = _event("CreateBucket", {
        "requestParameters": {"bucketName": "bucket-sans-tags"}
    })

    with patch.dict(os.environ, {"DRY_RUN": "false"}):
        handler = load_handler()
        result  = handler.lambda_handler(event, None)
        body    = json.loads(result["body"])

    assert body["s3"][0]["status"] == "tagged"

    tags_resp = s3.get_bucket_tagging(Bucket="bucket-sans-tags")
    tag_keys  = {t["Key"] for t in tags_resp["TagSet"]}
    assert {"Owner", "Squad", "CostCenter", "Environment"}.issubset(tag_keys)


@mock_aws
def test_s3_deja_conforme_est_idempotent():
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(
        Bucket="bucket-conforme",
        CreateBucketConfiguration={"LocationConstraint": REGION}
    )
    s3.put_bucket_tagging(
        Bucket="bucket-conforme",
        Tagging={"TagSet": [
            {"Key": "Owner",       "Value": "paul@entreprise.com"},
            {"Key": "Squad",       "Value": "Data"},
            {"Key": "CostCenter",  "Value": "CC-123"},
            {"Key": "Environment", "Value": "dev"},
        ]}
    )

    event = _event("CreateBucket", {
        "requestParameters": {"bucketName": "bucket-conforme"}
    })

    with patch.dict(os.environ, {"DRY_RUN": "false"}):
        handler = load_handler()
        result  = handler.lambda_handler(event, None)
        body    = json.loads(result["body"])

    assert body["s3"][0]["status"] == "already_compliant"


# ── Lambda function ───────────────────────────────────────────────────────────

@mock_aws
def test_lambda_function_applique_tags_en_mode_reel():
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
        FunctionName="ma-fonction",
        Runtime="python3.12",
        Role=f"arn:aws:iam::{ACCOUNT_ID}:role/test-role",
        Handler="index.handler",
        Code={"ZipFile": b"fake"},
    )
    func_arn = func["FunctionArn"]

    event = _event("CreateFunction20150331", {
        "requestParameters": {"functionName": "ma-fonction"},
        "responseElements":  {"functionArn": func_arn},
    })

    with patch.dict(os.environ, {"DRY_RUN": "false"}):
        handler = load_handler()
        result  = handler.lambda_handler(event, None)
        body    = json.loads(result["body"])

    assert body["lambda"][0]["status"] == "tagged"

    tags = lam.list_tags(Resource=func_arn)["Tags"]
    assert {"Owner", "Squad", "CostCenter", "Environment"}.issubset(tags.keys())


# ── Tags partiels ─────────────────────────────────────────────────────────────

@mock_aws
def test_ec2_tags_partiels_complete_uniquement_manquants():
    """Si Owner est déjà là, seuls Squad/CostCenter/Environment sont ajoutés."""
    ec2  = boto3.client("ec2", region_name=REGION)
    resp = ec2.run_instances(
        ImageId="ami-12345678", MinCount=1, MaxCount=1,
        TagSpecifications=[{"ResourceType": "instance", "Tags": [
            {"Key": "Owner", "Value": "existant@entreprise.com"},
        ]}]
    )
    iid = resp["Instances"][0]["InstanceId"]

    event = _event("RunInstances", {
        "responseElements": {
            "instancesSet": {"items": [{"instanceId": iid}]}
        }
    })

    with patch.dict(os.environ, {"DRY_RUN": "false"}):
        handler = load_handler()
        result  = handler.lambda_handler(event, None)
        body    = json.loads(result["body"])

    added = set(body["ec2"][0]["tags_added"])
    assert "Owner" not in added
    assert {"Squad", "CostCenter", "Environment"}.issubset(added)

    tags_resp = ec2.describe_instances(InstanceIds=[iid])
    tag_map   = {t["Key"]: t["Value"]
                 for t in tags_resp["Reservations"][0]["Instances"][0].get("Tags", [])}
    # Owner original préservé
    assert tag_map["Owner"] == "existant@entreprise.com"
