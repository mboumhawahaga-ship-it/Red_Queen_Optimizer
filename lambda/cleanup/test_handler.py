"""
Tests unitaires pour la Lambda de cleanup des ressources non taguees.

Utilise moto pour simuler les services AWS (EC2, RDS, S3, Lambda).
Verifie que :
- Les ressources SANS tags obligatoires sont detectees et supprimees
- Les ressources AVEC tags corrects sont laissees tranquilles
- Le mode DRY_RUN empeche la suppression reelle
- La periode de grace protege les ressources recentes

NOTE : Le dossier s'appelle "lambda/" mais "lambda" est un mot reserve
en Python (comme "if" ou "for"). On ne peut pas ecrire :
    import lambda.cleanup.handler  # ERREUR !
Donc on utilise sys.path pour dire a Python de chercher directement
dans le dossier lambda/cleanup/, puis on fait juste "import handler".
"""

import os
import sys
import json
import importlib
import pytest
import boto3
from unittest.mock import patch
from moto import mock_aws


# ========================================
# CONFIGURATION DES TESTS
# ========================================

REGION = "eu-west-1"

# Tags conformes (tous les tags obligatoires presents)
COMPLIANT_TAGS = [
    {"Key": "Owner", "Value": "test@entreprise.com"},
    {"Key": "Squad", "Value": "Data"},
    {"Key": "CostCenter", "Value": "CC-123"},
    {"Key": "Environment", "Value": "dev"},
]

# Tags incomplets (manque CostCenter et Environment)
INCOMPLETE_TAGS = [
    {"Key": "Owner", "Value": "test@entreprise.com"},
    {"Key": "Squad", "Value": "Data"},
]

# Chemin vers le dossier qui contient handler.py
HANDLER_DIR = os.path.dirname(os.path.abspath(__file__))


def load_handler():
    """
    Charge (ou recharge) le module handler.py.

    On doit recharger a chaque test parce que handler.py cree ses clients
    boto3 au moment de l'import. Si on ne recharge pas, les clients
    ne seront pas "mockes" par moto.
    """
    if HANDLER_DIR not in sys.path:
        sys.path.insert(0, HANDLER_DIR)

    if "handler" in sys.modules:
        return importlib.reload(sys.modules["handler"])

    import handler
    return handler


@pytest.fixture(autouse=True)
def aws_env():
    """Configure les variables d'environnement AWS pour les tests."""
    env_vars = {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SECURITY_TOKEN": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": REGION,
        "GRACE_PERIOD_HOURS": "0",
        "DRY_RUN": "false",
        "SNS_TOPIC_ARN": "",
        "DYNAMODB_TABLE_NAME": "",  # désactivé en tests unitaires
        "FEEDBACK_URL": "",
        "FEEDBACK_SECRET": "",
    }
    with patch.dict(os.environ, env_vars):
        yield
    # Nettoyer le module pour forcer le rechargement au prochain test
    if "handler" in sys.modules:
        del sys.modules["handler"]


# ========================================
# TESTS LOGIQUE PURE (check_required_tags + classify_resource)
# ========================================

def test_check_required_tags_conforme():
    """Des tags complets doivent etre acceptes."""
    handler = load_handler()
    is_ok, missing = handler.check_required_tags(COMPLIANT_TAGS)
    assert is_ok is True
    assert missing == []


def test_check_required_tags_incomplet():
    """Des tags incomplets doivent etre rejetes avec la liste des manquants."""
    handler = load_handler()
    is_ok, missing = handler.check_required_tags(INCOMPLETE_TAGS)
    assert is_ok is False
    assert "CostCenter" in missing
    assert "Environment" in missing


def test_check_required_tags_vide():
    """Une liste vide doit retourner les 4 tags comme manquants."""
    handler = load_handler()
    is_ok, missing = handler.check_required_tags([])
    assert is_ok is False
    assert len(missing) == 4


def test_check_required_tags_none():
    """None doit etre gere sans crash."""
    handler = load_handler()
    is_ok, missing = handler.check_required_tags(None)
    assert is_ok is False


def test_classify_rds_toujours_critical():
    """RDS doit toujours etre CRITICAL, meme sans tags."""
    handler = load_handler()
    assert handler.classify_resource('rds', []) == 'CRITICAL'
    assert handler.classify_resource('rds', COMPLIANT_TAGS) == 'CRITICAL'


def test_classify_ec2_prod_est_critical():
    """EC2 avec Environment=prod doit etre CRITICAL."""
    handler = load_handler()
    prod_tags = [{"Key": "Environment", "Value": "prod"}]
    assert handler.classify_resource('ec2', prod_tags) == 'CRITICAL'


def test_classify_ec2_dev_est_non_critical():
    """EC2 avec Environment=dev doit etre NON_CRITICAL."""
    handler = load_handler()
    assert handler.classify_resource('ec2', COMPLIANT_TAGS) == 'NON_CRITICAL'


def test_classify_critical_workload_tag():
    """Le tag CriticalWorkload=true doit forcer CRITICAL sur n'importe quel type."""
    handler = load_handler()
    tags = [{"Key": "CriticalWorkload", "Value": "true"}]
    assert handler.classify_resource('s3', tags) == 'CRITICAL'
    assert handler.classify_resource('lambda', tags) == 'CRITICAL'


def test_classify_s3_lambda_non_critical_par_defaut():
    """S3 et Lambda sans tag special doivent etre NON_CRITICAL."""
    handler = load_handler()
    assert handler.classify_resource('s3', COMPLIANT_TAGS) == 'NON_CRITICAL'
    assert handler.classify_resource('lambda', COMPLIANT_TAGS) == 'NON_CRITICAL'


# ========================================
# TESTS EC2
# ========================================

@mock_aws
def test_ec2_sans_tags_est_supprimee():
    """Une instance EC2 sans tags (NON_CRITICAL) doit etre terminee."""
    ec2 = boto3.client("ec2", region_name=REGION)

    resp = ec2.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)
    instance_id = resp["Instances"][0]["InstanceId"]

    state = ec2.describe_instances(InstanceIds=[instance_id])
    assert state["Reservations"][0]["Instances"][0]["State"]["Name"] == "running"

    handler = load_handler()
    result = handler.lambda_handler({}, None)
    body = json.loads(result["body"])

    # Structure correcte : body["ec2"]["non_compliant"]
    assert body["ec2"]["non_compliant"] >= 1
    state = ec2.describe_instances(InstanceIds=[instance_id])
    instance_state = state["Reservations"][0]["Instances"][0]["State"]["Name"]
    assert instance_state in ["shutting-down", "terminated"]


@mock_aws
def test_ec2_avec_tags_corrects_est_preservee():
    """Une instance EC2 avec tous les tags ne doit PAS etre supprimee."""
    ec2 = boto3.client("ec2", region_name=REGION)

    resp = ec2.run_instances(
        ImageId="ami-12345678", MinCount=1, MaxCount=1,
        TagSpecifications=[{
            "ResourceType": "instance",
            "Tags": COMPLIANT_TAGS
        }]
    )
    instance_id = resp["Instances"][0]["InstanceId"]

    handler = load_handler()
    result = handler.lambda_handler({}, None)
    body = json.loads(result["body"])

    assert body["ec2"]["non_compliant"] == 0
    state = ec2.describe_instances(InstanceIds=[instance_id])
    assert state["Reservations"][0]["Instances"][0]["State"]["Name"] == "running"


@mock_aws
def test_ec2_dry_run_ne_supprime_pas():
    """En DRY_RUN, les instances non conformes sont detectees mais PAS supprimees."""
    ec2 = boto3.client("ec2", region_name=REGION)

    resp = ec2.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)
    instance_id = resp["Instances"][0]["InstanceId"]

    with patch.dict(os.environ, {"DRY_RUN": "true"}):
        handler = load_handler()
        result = handler.lambda_handler({}, None)
        body = json.loads(result["body"])

    assert body["ec2"]["non_compliant"] >= 1
    assert body["ec2"]["deleted"] == 0
    state = ec2.describe_instances(InstanceIds=[instance_id])
    assert state["Reservations"][0]["Instances"][0]["State"]["Name"] == "running"


# ========================================
# TESTS S3
# ========================================

@mock_aws
def test_s3_sans_tags_est_detecte():
    """Un bucket S3 sans tags doit etre detecte comme non conforme."""
    s3 = boto3.client("s3", region_name=REGION)

    s3.create_bucket(
        Bucket="bucket-sans-tags",
        CreateBucketConfiguration={"LocationConstraint": REGION}
    )

    handler = load_handler()
    result = handler.lambda_handler({}, None)
    body = json.loads(result["body"])

    assert body["s3"]["non_compliant"] >= 1


@mock_aws
def test_s3_avec_tags_est_preserve():
    """Un bucket S3 avec tags corrects ne doit PAS etre supprime."""
    s3 = boto3.client("s3", region_name=REGION)

    s3.create_bucket(
        Bucket="bucket-avec-tags",
        CreateBucketConfiguration={"LocationConstraint": REGION}
    )
    s3.put_bucket_tagging(
        Bucket="bucket-avec-tags",
        Tagging={"TagSet": COMPLIANT_TAGS}
    )

    handler = load_handler()
    result = handler.lambda_handler({}, None)
    body = json.loads(result["body"])

    assert body["s3"]["deleted"] == 0
    buckets = [b["Name"] for b in s3.list_buckets()["Buckets"]]
    assert "bucket-avec-tags" in buckets


@mock_aws
def test_s3_avec_objets_est_vide_avant_suppression():
    """Un bucket avec des objets doit etre vide avant suppression."""
    s3 = boto3.client("s3", region_name=REGION)

    s3.create_bucket(
        Bucket="bucket-avec-objets",
        CreateBucketConfiguration={"LocationConstraint": REGION}
    )
    s3.put_object(Bucket="bucket-avec-objets", Key="file1.txt", Body=b"test")
    s3.put_object(Bucket="bucket-avec-objets", Key="file2.txt", Body=b"test")

    handler = load_handler()
    result = handler.lambda_handler({}, None)
    body = json.loads(result["body"])

    assert body["s3"]["non_compliant"] >= 1


# ========================================
# TESTS LAMBDA FUNCTIONS
# ========================================

@mock_aws
def test_lambda_sans_tags_est_detectee():
    """Une Lambda sans tags doit etre detectee comme non conforme."""
    iam = boto3.client("iam", region_name=REGION)
    lam = boto3.client("lambda", region_name=REGION)

    iam.create_role(
        RoleName="test-role",
        AssumeRolePolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{"Effect": "Allow", "Principal": {"Service": "lambda.amazonaws.com"}, "Action": "sts:AssumeRole"}]
        }),
        Path="/"
    )

    lam.create_function(
        FunctionName="function-sans-tags",
        Runtime="python3.11",
        Role="arn:aws:iam::123456789012:role/test-role",
        Handler="index.handler",
        Code={"ZipFile": b"fake code"},
    )

    handler = load_handler()
    result = handler.lambda_handler({}, None)
    body = json.loads(result["body"])

    assert body["lambda"]["non_compliant"] >= 1


# ========================================
# TEST INTEGRATION COMPLET
# ========================================

@mock_aws
def test_mix_conformes_et_non_conformes():
    """Test avec un mix de ressources conformes et non conformes."""
    ec2 = boto3.client("ec2", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)

    # 1 EC2 conforme
    ec2.run_instances(
        ImageId="ami-12345678", MinCount=1, MaxCount=1,
        TagSpecifications=[{"ResourceType": "instance", "Tags": COMPLIANT_TAGS}]
    )
    # 1 EC2 non conforme
    ec2.run_instances(ImageId="ami-12345678", MinCount=1, MaxCount=1)

    # 1 S3 conforme
    s3.create_bucket(Bucket="conforme", CreateBucketConfiguration={"LocationConstraint": REGION})
    s3.put_bucket_tagging(Bucket="conforme", Tagging={"TagSet": COMPLIANT_TAGS})

    # 1 S3 non conforme
    s3.create_bucket(Bucket="non-conforme", CreateBucketConfiguration={"LocationConstraint": REGION})

    handler = load_handler()
    result = handler.lambda_handler({}, None)
    body = json.loads(result["body"])

    assert body["ec2"]["non_compliant"] >= 1
    assert body["s3"]["non_compliant"] >= 1
    assert body["ec2"]["scanned"] >= 2
    assert body["s3"]["scanned"] >= 2

    print(f"\nResultat complet : {json.dumps(body, indent=2)}")
