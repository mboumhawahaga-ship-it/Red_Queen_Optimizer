"""
🤖 MODULE DE GOUVERNANCE AWS - CLEANUP AUTOMATIQUE
Scanne EC2, RDS, S3 et Lambda pour supprimer le non-conforme.
Classifie chaque ressource en CRITICAL / NON_CRITICAL et persiste
l'état dans DynamoDB (redqueen-governance-state).
Envoie des emails SNS avec liens de feedback signés (approve/reject/tag).
"""

import os
import json
import time
import hmac
import hashlib
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple

import boto3
from botocore.exceptions import ClientError

# --- CONFIGURATION ---
REQUIRED_TAGS       = ["Owner", "Squad", "CostCenter", "Environment"]
GRACE_PERIOD_HOURS  = int(os.environ.get("GRACE_PERIOD_HOURS", "24"))
DRY_RUN             = os.environ.get("DRY_RUN", "true").lower() == "true"
SNS_TOPIC_ARN       = os.environ.get("SNS_TOPIC_ARN", "")
DYNAMODB_TABLE_NAME = os.environ.get("DYNAMODB_TABLE_NAME", "")
FEEDBACK_URL        = os.environ.get("FEEDBACK_URL", "")
FEEDBACK_SECRET     = os.environ.get("FEEDBACK_SECRET", "")

CRITICAL_RESOURCE_TYPES = {"rds"}

# --- CLIENTS AWS ---
ec2_client    = boto3.client('ec2')
rds_client    = boto3.client('rds')
s3_client     = boto3.client('s3')
lambda_client = boto3.client('lambda')
sns_client    = boto3.client('sns')
dynamodb      = boto3.resource('dynamodb')


# ========================================
# POINT D'ENTRÉE
# ========================================

def lambda_handler(event, context):
    """Point d'entrée principal de la Lambda."""
    print(f"🚀 Démarrage du cleanup - DRY_RUN={DRY_RUN}")

    non_compliant_details: List[Dict] = []

    global_results = {
        "ec2":    cleanup_ec2_instances(non_compliant_details),
        "rds":    cleanup_rds_instances(non_compliant_details),
        "s3":     cleanup_s3_buckets(non_compliant_details),
        "lambda": cleanup_lambda_functions(non_compliant_details),
        "non_compliant_details": non_compliant_details,
        "errors": [],
    }

    send_notification(global_results)

    return {
        'statusCode': 200,
        'body': json.dumps(global_results, default=str)
    }


# ========================================
# CLASSIFICATION
# ========================================

def classify_resource(resource_type: str, tags: List[Dict]) -> str:
    """
    Retourne CRITICAL ou NON_CRITICAL selon le type et les tags.
    - RDS              → toujours CRITICAL
    - EC2 en prod      → CRITICAL
    - CriticalWorkload=true → CRITICAL
    - Tout le reste    → NON_CRITICAL
    """
    tag_map = {t.get('Key'): t.get('Value') for t in (tags or [])}

    if tag_map.get('CriticalWorkload', '').lower() == 'true':
        return 'CRITICAL'
    if resource_type in CRITICAL_RESOURCE_TYPES:
        return 'CRITICAL'
    if resource_type == 'ec2' and tag_map.get('Environment') == 'prod':
        return 'CRITICAL'
    return 'NON_CRITICAL'


# ========================================
# PERSISTANCE DYNAMODB
# ========================================

def persist_to_dynamodb(resource_id: str, resource_type: str,
                        criticality: str, compliant: bool,
                        missing_tags: List[str], environment: str):
    """Écrit ou met à jour l'état d'une ressource dans DynamoDB."""
    if not DYNAMODB_TABLE_NAME:
        return
    try:
        table      = dynamodb.Table(DYNAMODB_TABLE_NAME)
        now        = datetime.utcnow().isoformat()
        ttl_expiry = int(time.time()) + (90 * 24 * 3600)

        table.put_item(Item={
            'resource_id':    resource_id,
            'scan_timestamp': now,
            'resource_type':  resource_type,
            'criticality':    criticality,
            'compliant':      compliant,
            'missing_tags':   missing_tags,
            'environment':    environment,
            'dry_run':        DRY_RUN,
            'ttl_expiry':     ttl_expiry,
        })
    except Exception as e:
        print(f"⚠️  DynamoDB write error ({resource_id}): {e}")


# ========================================
# SCAN & CLEANUP
# ========================================

def cleanup_ec2_instances(non_compliant_details: List[Dict]) -> Dict[str, Any]:
    """Nettoie les instances EC2 non conformes."""
    print("🖥️  Scan EC2...")
    res = {
        "scanned": 0, "already_terminated": 0,
        "non_compliant": 0, "deleted": 0, "in_grace_period": 0,
        "critical": 0, "non_critical": 0
    }
    try:
        paginator = ec2_client.get_paginator('describe_instances')
        for page in paginator.paginate():
            for reservation in page['Reservations']:
                for instance in reservation['Instances']:
                    instance_id = instance['InstanceId']
                    state       = instance.get('State', {}).get('Name')
                    res["scanned"] += 1

                    if state in ['terminated', 'terminating']:
                        res["already_terminated"] += 1
                        continue

                    tags        = instance.get('Tags', [])
                    compliant, missing = check_required_tags(tags)
                    tag_map     = {t.get('Key'): t.get('Value') for t in tags}
                    environment = tag_map.get('Environment', 'unknown')
                    criticality = classify_resource('ec2', tags)

                    if criticality == 'CRITICAL':
                        res["critical"] += 1
                    else:
                        res["non_critical"] += 1

                    if not compliant:
                        res["non_compliant"] += 1
                        non_compliant_details.append({
                            "resource_id":   instance_id,
                            "resource_type": "EC2",
                            "criticality":   criticality,
                            "missing_tags":  missing,
                        })
                        persist_to_dynamodb(
                            instance_id, 'EC2', criticality,
                            False, missing, environment
                        )
                        launch_time = instance.get('LaunchTime')
                        if is_within_grace_period(launch_time):
                            res["in_grace_period"] += 1
                            continue
                        if not DRY_RUN and criticality == 'NON_CRITICAL':
                            ec2_client.terminate_instances(InstanceIds=[instance_id])
                            res["deleted"] += 1
                    else:
                        persist_to_dynamodb(
                            instance_id, 'EC2', criticality,
                            True, [], environment
                        )
    except Exception as e:
        res["errors"] = str(e)
    return res


def cleanup_rds_instances(non_compliant_details: List[Dict]) -> Dict[str, Any]:
    """Nettoie les instances RDS non conformes."""
    print("🗄️  Scan RDS...")
    res = {
        "scanned": 0, "already_deleted": 0,
        "non_compliant": 0, "deleted": 0, "in_grace_period": 0,
        "critical": 0, "non_critical": 0
    }
    try:
        paginator = rds_client.get_paginator('describe_db_instances')
        for page in paginator.paginate():
            for db in page['DBInstances']:
                res["scanned"] += 1
                if db['DBInstanceStatus'] in ['deleting', 'deleted']:
                    res["already_deleted"] += 1
                    continue

                t_resp = rds_client.list_tags_for_resource(
                    ResourceName=db['DBInstanceArn']
                )
                tags        = t_resp.get('TagList', [])
                compliant, missing = check_required_tags(tags)
                tag_map     = {t.get('Key'): t.get('Value') for t in tags}
                environment = tag_map.get('Environment', 'unknown')
                criticality = classify_resource('rds', tags)
                res["critical"] += 1

                if not compliant:
                    res["non_compliant"] += 1
                    non_compliant_details.append({
                        "resource_id":   db['DBInstanceIdentifier'],
                        "resource_type": "RDS",
                        "criticality":   criticality,
                        "missing_tags":  missing,
                    })
                    persist_to_dynamodb(
                        db['DBInstanceIdentifier'], 'RDS', criticality,
                        False, missing, environment
                    )
                    create_time = db.get('InstanceCreateTime')
                    if is_within_grace_period(create_time):
                        res["in_grace_period"] += 1
                        continue
                    # RDS CRITICAL → jamais supprimé automatiquement
                    print(f"⛔ RDS CRITICAL non conforme ignorée : {db['DBInstanceIdentifier']}")
                else:
                    persist_to_dynamodb(
                        db['DBInstanceIdentifier'], 'RDS', criticality,
                        True, [], environment
                    )
    except Exception as e:
        res["errors"] = str(e)
    return res


def cleanup_s3_buckets(non_compliant_details: List[Dict]) -> Dict[str, Any]:
    """Nettoie les buckets S3 non conformes."""
    print("🪣  Scan S3...")
    res = {
        "scanned": 0, "non_compliant": 0, "deleted": 0,
        "critical": 0, "non_critical": 0
    }
    try:
        for b in s3_client.list_buckets()['Buckets']:
            name = b['Name']
            res["scanned"] += 1
            try:
                tags_resp = s3_client.get_bucket_tagging(Bucket=name)
                tags = tags_resp.get('TagSet', [])
            except ClientError:
                tags = []

            compliant, missing = check_required_tags(tags)
            tag_map     = {t.get('Key'): t.get('Value') for t in tags}
            environment = tag_map.get('Environment', 'unknown')
            criticality = classify_resource('s3', tags)

            if criticality == 'CRITICAL':
                res["critical"] += 1
            else:
                res["non_critical"] += 1

            if not compliant:
                res["non_compliant"] += 1
                non_compliant_details.append({
                    "resource_id":   name,
                    "resource_type": "S3",
                    "criticality":   criticality,
                    "missing_tags":  missing,
                })
                persist_to_dynamodb(
                    name, 'S3', criticality, False, missing, environment
                )
                if is_within_grace_period(b.get('CreationDate')):
                    continue
                if not DRY_RUN and criticality == 'NON_CRITICAL':
                    delete_all_objects_in_bucket(name)
                    s3_client.delete_bucket(Bucket=name)
                    res["deleted"] += 1
            else:
                persist_to_dynamodb(
                    name, 'S3', criticality, True, [], environment
                )
    except Exception as e:
        res["errors"] = str(e)
    return res


def cleanup_lambda_functions(non_compliant_details: List[Dict]) -> Dict[str, Any]:
    """Nettoie les fonctions Lambda non conformes."""
    print("⚡ Scan Lambda...")
    res = {
        "scanned": 0, "non_compliant": 0, "deleted": 0,
        "critical": 0, "non_critical": 0
    }
    try:
        paginator = lambda_client.get_paginator('list_functions')
        for page in paginator.paginate():
            for f in page['Functions']:
                name = f['FunctionName']
                if name == os.environ.get('AWS_LAMBDA_FUNCTION_NAME'):
                    continue
                res["scanned"] += 1

                t_resp = lambda_client.list_tags(Resource=f['FunctionArn'])
                tags   = [{'Key': k, 'Value': v}
                          for k, v in t_resp.get('Tags', {}).items()]

                compliant, missing = check_required_tags(tags)
                tag_map     = {t.get('Key'): t.get('Value') for t in tags}
                environment = tag_map.get('Environment', 'unknown')
                criticality = classify_resource('lambda', tags)

                if criticality == 'CRITICAL':
                    res["critical"] += 1
                else:
                    res["non_critical"] += 1

                if not compliant:
                    res["non_compliant"] += 1
                    non_compliant_details.append({
                        "resource_id":   name,
                        "resource_type": "Lambda",
                        "criticality":   criticality,
                        "missing_tags":  missing,
                    })
                    persist_to_dynamodb(
                        name, 'Lambda', criticality, False, missing, environment
                    )
                    if not DRY_RUN and criticality == 'NON_CRITICAL':
                        lambda_client.delete_function(FunctionName=name)
                        res["deleted"] += 1
                else:
                    persist_to_dynamodb(
                        name, 'Lambda', criticality, True, [], environment
                    )
    except Exception as e:
        res["errors"] = str(e)
    return res


# ========================================
# UTILITAIRES
# ========================================

def check_required_tags(tags: List[Dict]) -> Tuple[bool, List[str]]:
    """Vérifie la présence des tags obligatoires."""
    keys    = [t.get('Key') for t in tags] if tags else []
    missing = [t for t in REQUIRED_TAGS if t not in keys]
    return len(missing) == 0, missing


def is_within_grace_period(creation_time: datetime) -> bool:
    """Vérifie si la ressource est encore sous période de grâce."""
    if not creation_time:
        return False
    delta = timedelta(hours=GRACE_PERIOD_HOURS)
    now   = datetime.now(creation_time.tzinfo)
    return now - creation_time < delta


def delete_all_objects_in_bucket(bucket_name: str):
    """Vide un bucket S3 de tous ses objets et versions."""
    paginator = s3_client.get_paginator('list_object_versions')
    for page in paginator.paginate(Bucket=bucket_name):
        versions = page.get('Versions', [])
        markers  = page.get('DeleteMarkers', [])
        objs     = [
            {'Key': v['Key'], 'VersionId': v['VersionId']}
            for v in versions + markers
        ]
        if objs:
            s3_client.delete_objects(
                Bucket=bucket_name,
                Delete={'Objects': objs}
            )


# ========================================
# FEEDBACK LINKS
# ========================================

def _build_feedback_links(resource_id: str, resource_type: str,
                           missing_tags: List[str]) -> str:
    """Construit les 3 liens de feedback signés pour un email SNS."""
    if not FEEDBACK_URL:
        return ""

    def _token(action: str) -> str:
        if not FEEDBACK_SECRET:
            return "no-secret"
        payload = f"{resource_id}:{action}"
        return hmac.new(
            FEEDBACK_SECRET.encode(),
            payload.encode(),
            hashlib.sha256
        ).hexdigest()

    base = FEEDBACK_URL.rstrip("/")
    rt   = resource_type.upper()

    approve_url = (
        f"{base}?resource_id={resource_id}&resource_type={rt}"
        f"&action=approve&token={_token('approve')}"
    )
    reject_url = (
        f"{base}?resource_id={resource_id}&resource_type={rt}"
        f"&action=reject&token={_token('reject')}"
    )
    tag_url = (
        f"{base}?resource_id={resource_id}&resource_type={rt}"
        f"&action=tag&token={_token('tag')}"
        f"&owner=VOTRE_EMAIL&squad=VOTRE_SQUAD"
        f"&cost_center=CC-XXX&environment=dev"
    )

    lines = []
    if missing_tags:
        lines.append(f"  Tags manquants : {', '.join(missing_tags)}")
    lines += [
        f"  ✅ Approuver (ressource légitime) :",
        f"     {approve_url}",
        f"  🗑️  Rejeter (peut être supprimée) :",
        f"     {reject_url}",
        f"  🏷️  Tagger maintenant (complétez les paramètres) :",
        f"     {tag_url}",
    ]
    return "\n".join(lines)


# ========================================
# NOTIFICATION SNS
# ========================================

def send_notification(res: Dict):
    """Envoie le rapport final via SNS avec liens de feedback."""
    if not SNS_TOPIC_ARN:
        return

    mode = 'SIMULATION' if DRY_RUN else 'PROD'
    msg  = f"Rapport Cleanup AWS ({mode})\n{'='*50}\n\n"

    for s in ['ec2', 'rds', 's3', 'lambda']:
        data = res[s]
        msg += (
            f"[{s.upper()}] {data.get('scanned', 0)} scannées — "
            f"{data.get('non_compliant', 0)} non conformes "
            f"(CRITICAL: {data.get('critical', 0)}, "
            f"NON_CRITICAL: {data.get('non_critical', 0)}), "
            f"{data.get('deleted', 0)} supprimées\n"
        )

    non_compliant_resources = res.get("non_compliant_details", [])
    if non_compliant_resources and FEEDBACK_URL:
        msg += f"\n{'─'*50}\n"
        msg += "Actions requises — cliquez sur un lien pour chaque ressource :\n\n"
        for r in non_compliant_resources[:10]:
            msg += (
                f"Ressource : {r['resource_type']} {r['resource_id']} "
                f"[{r.get('criticality', '?')}]\n"
            )
            msg += _build_feedback_links(
                r['resource_id'],
                r['resource_type'],
                r.get('missing_tags', [])
            )
            msg += "\n\n"
        if len(non_compliant_resources) > 10:
            msg += f"... et {len(non_compliant_resources) - 10} autres ressources.\n"

    sns_client.publish(
        TopicArn=SNS_TOPIC_ARN,
        Subject=f"[Red Queen] Rapport Gouvernance AWS ({mode})",
        Message=msg
    )
