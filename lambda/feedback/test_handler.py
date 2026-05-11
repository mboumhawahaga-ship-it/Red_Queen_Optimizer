"""
Tests unitaires pour lambda/feedback/handler.py

Couvre :
- Token HMAC valide / invalide
- Action approve  → 200 + DynamoDB
- Action reject   → 200 + DynamoDB
- Action tag      → tags appliqués sur EC2/S3 + DynamoDB
- Idempotence     → action déjà traitée retourne 200 sans re-exécuter
- DRY_RUN=true    → aucune modification AWS
- Paramètres manquants → 400
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
SECRET      = "test-secret-key"
HANDLER_DIR = os.path.dirname(os.path.abspath(__file__))


# ── Helpers ───────────────────────────────────────────────────────────────────

def load_handler():
    # Retire tout autre dossier lambda du path pour éviter de charger
    # le mauvais handler.py (ex: cleanup/handler.py)
    for p in list(sys.path):
        if "lambda" in p.replace("\\", "/") and p != HANDLER_DIR:
            sys.path.remove(p)
    if HANDLER_DIR not in sys.path:
        sys.path.insert(0, HANDLER_DIR)
    if "handler" in sys.modules:
        return importlib.reload(sys.modules["handler"])
    import handler
    return handler


def _event(resource_id: str, action: str, extra: dict = None,
           token: str = None) -> dict:
    """Construit un event Lambda URL v2."""
    h = load_handler()
    tok = token if token is not None else h.generate_token(resource_id, action)
    params = {
        "resource_id":   resource_id,
        "resource_type": "EC2",
        "action":        action,
        "token":         tok,
    }
    if extra:
        params.update(extra)
    return {"queryStringParameters": params}


@pytest.fixture(autouse=True)
def aws_env():
    env_vars = {
        "AWS_ACCESS_KEY_ID":     "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SECURITY_TOKEN":    "testing",
        "AWS_SESSION_TOKEN":     "testing",
        "AWS_DEFAULT_REGION":    REGION,
        "DYNAMODB_TABLE_NAME":   "",
        "FEEDBACK_SECRET":       SECRET,
        "DRY_RUN":               "true",
        "AWS_REGION":            REGION,
    }
    with patch.dict(os.environ, env_vars):
        yield
    if "handler" in sys.modules:
        del sys.modules["handler"]


# ── Token ─────────────────────────────────────────────────────────────────────

def test_token_valide_accepte():
    h     = load_handler()
    token = h.generate_token("i-123", "approve")
    assert h._verify_token(token, "i-123", "approve") is True


def test_token_invalide_rejete():
    h = load_handler()
    assert h._verify_token("mauvais-token", "i-123", "approve") is False


def test_token_mauvaise_action_rejete():
    h     = load_handler()
    token = h.generate_token("i-123", "approve")
    assert h._verify_token(token, "i-123", "reject") is False


def test_sans_secret_token_toujours_accepte():
    with patch.dict(os.environ, {"FEEDBACK_SECRET": ""}):
        h = load_handler()
        assert h._verify_token("n-importe-quoi", "i-123", "approve") is True


# ── Paramètres invalides ──────────────────────────────────────────────────────

def test_action_inconnue_retourne_400():
    h      = load_handler()
    event  = {"queryStringParameters": {
        "resource_id": "i-123", "action": "delete", "token": "x"
    }}
    result = h.lambda_handler(event, None)
    assert result["statusCode"] == 400


def test_resource_id_manquant_retourne_400():
    h      = load_handler()
    event  = {"queryStringParameters": {"action": "approve", "token": "x"}}
    result = h.lambda_handler(event, None)
    assert result["statusCode"] == 400


def test_token_invalide_retourne_403():
    h      = load_handler()
    result = h.lambda_handler(
        _event("i-123", "approve", token="mauvais"), None
    )
    assert result["statusCode"] == 403
    assert "expiré" in result["body"]


# ── Approve ───────────────────────────────────────────────────────────────────

def test_approve_retourne_200_avec_html():
    h      = load_handler()
    result = h.lambda_handler(_event("i-abc123", "approve"), None)
    assert result["statusCode"] == 200
    assert "approuvée" in result["body"]
    assert "i-abc123" in result["body"]


def test_approve_content_type_html():
    h      = load_handler()
    result = h.lambda_handler(_event("i-abc123", "approve"), None)
    assert "text/html" in result["headers"]["Content-Type"]


# ── Reject ────────────────────────────────────────────────────────────────────

def test_reject_retourne_200_avec_html():
    h      = load_handler()
    result = h.lambda_handler(_event("i-abc123", "reject"), None)
    assert result["statusCode"] == 200
    assert "suppression" in result["body"]


# ── Tag ───────────────────────────────────────────────────────────────────────

def test_tag_dry_run_retourne_200_sans_modifier_aws():
    h      = load_handler()
    result = h.lambda_handler(_event("i-abc123", "tag", {
        "owner":       "jean@entreprise.com",
        "squad":       "Data",
        "cost_center": "CC-123",
        "environment": "dev",
    }), None)
    assert result["statusCode"] == 200
    assert "simulation" in result["body"].lower() or "DRY_RUN" in result["body"]


def test_tag_params_manquants_retourne_400():
    h      = load_handler()
    result = h.lambda_handler(_event("i-abc123", "tag", {
        "owner": "jean@entreprise.com",
        # squad et cost_center manquants
    }), None)
    assert result["statusCode"] == 400
    assert "incomplets" in result["body"]


@mock_aws
def test_tag_applique_tags_ec2_en_mode_reel():
    ec2  = boto3.client("ec2", region_name=REGION)
    resp = ec2.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)
    iid  = resp["Instances"][0]["InstanceId"]

    with patch.dict(os.environ, {"DRY_RUN": "false"}):
        h      = load_handler()
        result = h.lambda_handler(_event(iid, "tag", {
            "resource_type": "EC2",
            "owner":         "paul@entreprise.com",
            "squad":         "Backend",
            "cost_center":   "CC-456",
            "environment":   "dev",
        }), None)

    assert result["statusCode"] == 200
    assert "Tags appliqués" in result["body"]

    tags_resp = ec2.describe_instances(InstanceIds=[iid])
    tag_map   = {t["Key"]: t["Value"]
                 for t in tags_resp["Reservations"][0]["Instances"][0].get("Tags", [])}
    assert tag_map["Owner"]      == "paul@entreprise.com"
    assert tag_map["Squad"]      == "Backend"
    assert tag_map["CostCenter"] == "CC-456"


@mock_aws
def test_tag_applique_tags_s3_en_mode_reel():
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(
        Bucket="mon-bucket",
        CreateBucketConfiguration={"LocationConstraint": REGION}
    )

    with patch.dict(os.environ, {"DRY_RUN": "false"}):
        h      = load_handler()
        result = h.lambda_handler(_event("mon-bucket", "tag", {
            "resource_type": "S3",
            "owner":         "sophie@entreprise.com",
            "squad":         "DataEng",
            "cost_center":   "CC-789",
            "environment":   "dev",
        }), None)

    assert result["statusCode"] == 200

    tags_resp = s3.get_bucket_tagging(Bucket="mon-bucket")
    tag_map   = {t["Key"]: t["Value"] for t in tags_resp["TagSet"]}
    assert tag_map["Owner"] == "sophie@entreprise.com"
    assert tag_map["Squad"] == "DataEng"


# ── Idempotence ───────────────────────────────────────────────────────────────

@mock_aws
def test_idempotence_action_deja_traitee():
    """Si DynamoDB contient déjà un feedback, retourne 200 sans re-exécuter."""
    import boto3 as b3

    # Créer la table DynamoDB mockée
    ddb = b3.resource("dynamodb", region_name=REGION)
    ddb.create_table(
        TableName="redqueen-governance-state",
        KeySchema=[
            {"AttributeName": "resource_id",  "KeyType": "HASH"},
            {"AttributeName": "scan_timestamp", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "resource_id",   "AttributeType": "S"},
            {"AttributeName": "scan_timestamp", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    # Pré-remplir avec une action déjà effectuée
    table = ddb.Table("redqueen-governance-state")
    table.put_item(Item={
        "resource_id":     "i-already-done",
        "scan_timestamp":  "2024-01-01T00:00:00",
        "feedback_action": "approve",
        "resource_type":   "EC2",
        "event":           "feedback",
        "dry_run":         False,
        "environment":     "dev",
        "criticality":     "NON_CRITICAL",
        "compliant":       True,
        "ttl_expiry":      9999999999,
    })

    with patch.dict(os.environ, {"DYNAMODB_TABLE_NAME": "redqueen-governance-state",
                                  "DRY_RUN": "false"}):
        h      = load_handler()
        result = h.lambda_handler(_event("i-already-done", "reject"), None)

    assert result["statusCode"] == 200
    assert "déjà" in result["body"]
    # L'action reject ne doit PAS avoir écrasé l'approve
    item = table.query(
        KeyConditionExpression="resource_id = :r",
        ExpressionAttributeValues={":r": "i-already-done"},
        ScanIndexForward=False,
        Limit=1,
    )["Items"][0]
    assert item["feedback_action"] == "approve"


# ── DynamoDB persist ──────────────────────────────────────────────────────────

@mock_aws
def test_approve_persiste_dans_dynamodb():
    ddb = boto3.resource("dynamodb", region_name=REGION)
    ddb.create_table(
        TableName="redqueen-governance-state",
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

    with patch.dict(os.environ, {"DYNAMODB_TABLE_NAME": "redqueen-governance-state"}):
        h = load_handler()
        h.lambda_handler(_event("i-persist-test", "approve"), None)

    table = ddb.Table("redqueen-governance-state")
    items = table.query(
        KeyConditionExpression="resource_id = :r",
        ExpressionAttributeValues={":r": "i-persist-test"},
    )["Items"]
    assert len(items) == 1
    assert items[0]["feedback_action"] == "approve"
    assert items[0]["event"] == "feedback"
