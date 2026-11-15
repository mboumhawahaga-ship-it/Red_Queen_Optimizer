"""
Compliance Evaluator Lambda.

Triggered by:
  - EventBridge rule matching AWS Config NON_COMPLIANT changes
  - EventBridge Scheduler re-invoke at SLA deadline (detail-type = "SLADeadline")

Flow (initial trigger):
  1. Extract resource_id, resource_type, tags from event
  2. classify_resource → CRITICAL / NON_CRITICAL
  3. get_governance_record → skip if already 'handled'
  4. check_required_tags → if compliant, put record + metric + return
  5. send_sns + send_slack (tiered SLA)
  6. put_governance_record(event=alerted, sla_deadline=now+SLA_HOURS)
  7. schedule EventBridge Scheduler one-shot at sla_deadline
  8. put_governance_metric(outcome=alerted)

Every outcome (compliant / alerted / resolved / quarantined) also publishes a
CloudWatch metric in the RedQueen/Governance namespace for the Grafana dashboard.

Flow (SLA re-invoke):
  1. Re-check tags via AWS APIs
  2. If now compliant → put record(event=resolved) + delete scheduler
  3. If still non-compliant → apply quarantine tags + put record(event=quarantined)
"""

import os
import json
import logging
import re
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

from ..shared.utils import (
    sanitize_log_value,
    resource_in_governance_scope,
    check_required_tags,
    classify_resource,
    put_governance_record,
    get_governance_record,
    put_governance_metric,
    send_sns,
    send_slack,
    tags_list_to_dict,
)
from ..shared.constants import SLA_HOURS, SLACK_COLORS, TRACK_EMOJI, WATCHED_EVENTS

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ── Env vars ──────────────────────────────────────────────────────────────────

TABLE_NAME       = os.environ.get("GOVERNANCE_TABLE", "")
SNS_TOPIC_ARN    = os.environ.get("SNS_TOPIC_ARN", "")
SLACK_WEBHOOK    = os.environ.get("SLACK_WEBHOOK_URL", "")
SCHEDULER_GROUP  = os.environ.get("SCHEDULER_GROUP_NAME", "default")
SCHEDULER_ROLE   = os.environ.get("SCHEDULER_ROLE_ARN", "")
LAMBDA_ARN       = os.environ.get("AWS_LAMBDA_FUNCTION_ARN", "")
DRY_RUN          = os.environ.get("DRY_RUN", "true").lower() == "true"

# ── Entry point ───────────────────────────────────────────────────────────────


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    detail_type = event.get("detail-type", "")

    if detail_type == "SLADeadline":
        return _handle_sla_deadline(event)

    # Config NON_COMPLIANT or CloudTrail create event
    return _handle_compliance_event(event)


# ── Initial compliance evaluation ─────────────────────────────────────────────


def _handle_compliance_event(event: Dict[str, Any]) -> Dict[str, Any]:
    resource_id, resource_type, tags, region, account_id = _extract_event_fields(event)
    if not resource_id:
        logger.warning("compliance_event: missing resource_id — skipping")
        return {"status": "skipped", "reason": "missing_resource_id"}
    if not resource_in_governance_scope(resource_id, resource_type):
        return {"status": "skipped", "reason": "out_of_scope"}

    safe_id   = sanitize_log_value(resource_id)
    safe_type = sanitize_log_value(resource_type)

    # 1. classify
    criticality = classify_resource(resource_type, tags)
    logger.info("resource=%s type=%s criticality=%s", safe_id, safe_type, criticality)

    # 2. skip if already 'handled'
    existing = get_governance_record(TABLE_NAME, resource_id)
    feedback = get_governance_record(TABLE_NAME, resource_id, event_filter="feedback")
    if feedback and feedback.get("status") == "handled":
        return {"status": "skipped", "reason": "handled"}
    if existing and existing.get("status") == "handled":
        logger.info("resource=%s is handled — skipping evaluation", safe_id)
        return {"status": "skipped", "reason": "handled"}

    # 3. check tags
    compliant, missing = check_required_tags(tags)
    if compliant:
        put_governance_record(
            TABLE_NAME, resource_id, resource_type, "scan",
            criticality=criticality, compliant=True,
            environment=tags_list_to_dict(tags).get("Environment", "unknown"),
            dry_run=DRY_RUN,
        )
        put_governance_metric("compliant", resource_type, criticality, dry_run=DRY_RUN)
        logger.info("resource=%s is compliant", safe_id)
        return {"status": "compliant", "resource_id": resource_id}

    # Re-delivery must not reset the deadline or send another successful alert.
    if existing and existing.get("status") == "alerted" and existing.get("sla_deadline"):
        return {"status": "skipped", "reason": "already_alerted",
                "sla_deadline": existing["sla_deadline"]}

    # 4. SLA
    sla_hours    = SLA_HOURS[criticality]
    now          = datetime.now(timezone.utc)
    sla_deadline = (now + timedelta(hours=sla_hours)).isoformat()
    environment  = tags_list_to_dict(tags).get("Environment", "unknown")

    # Create the durable timer first. On retry, reuse its original deadline.
    if not DRY_RUN:
        sla_deadline = _schedule_sla_check(resource_id, resource_type, criticality, sla_deadline, region)

    # 5. notify
    _send_alert(
        resource_id, resource_type, criticality,
        missing, sla_hours, sla_deadline, account_id, region,
    )

    # 6. persist
    put_governance_record(
        TABLE_NAME, resource_id, resource_type, "alerted",
        criticality=criticality, compliant=False,
        missing_tags=missing,
        environment=environment,
        dry_run=DRY_RUN,
        extra={"status": "alerted", "sla_deadline": sla_deadline},
    )

    # 8. metric
    put_governance_metric("alerted", resource_type, criticality, dry_run=DRY_RUN)

    return {
        "status":       "alerted",
        "dry_run":      DRY_RUN,
        "resource_id":  resource_id,
        "criticality":  criticality,
        "missing_tags": missing,
        "sla_deadline": sla_deadline,
    }


# ── SLA deadline re-invoke ────────────────────────────────────────────────────


def _handle_sla_deadline(event: Dict[str, Any]) -> Dict[str, Any]:
    detail       = event.get("detail", {})
    resource_id  = detail.get("resource_id", "")
    resource_type = detail.get("resource_type", "")
    criticality  = detail.get("criticality", "NON_CRITICAL")
    schedule_name = detail.get("schedule_name", "")

    if not resource_id:
        logger.warning("sla_deadline: missing resource_id — skipping")
        return {"status": "skipped", "reason": "missing_resource_id"}
    if not resource_in_governance_scope(resource_id, resource_type):
        return {"status": "skipped", "reason": "out_of_scope"}

    safe_id = sanitize_log_value(resource_id)

    feedback = get_governance_record(TABLE_NAME, resource_id, event_filter="feedback")
    if feedback and feedback.get("status") == "handled":
        if schedule_name and not DRY_RUN:
            _delete_schedule(schedule_name)
        return {"status": "skipped", "reason": "handled"}
    deadline = detail.get("sla_deadline")
    if deadline and datetime.now(timezone.utc) < datetime.fromisoformat(deadline):
        return {"status": "skipped", "reason": "before_deadline"}

    # re-fetch current tags
    tags = _fetch_current_tags(resource_id, resource_type)
    compliant, missing = check_required_tags(tags)

    if compliant:
        put_governance_record(
            TABLE_NAME, resource_id, resource_type, "resolved",
            criticality=criticality, compliant=True,
            dry_run=DRY_RUN,
            extra={"status": "resolved"},
        )
        put_governance_metric("resolved", resource_type, criticality, dry_run=DRY_RUN)
        logger.info("resource=%s resolved before SLA deadline", safe_id)
        if schedule_name and not DRY_RUN:
            _delete_schedule(schedule_name)
        return {"status": "resolved", "resource_id": resource_id}

    # still non-compliant → quarantine (tags only, no deletion)
    if not DRY_RUN:
        _apply_quarantine_tags(resource_id, resource_type)

    put_governance_record(
        TABLE_NAME, resource_id, resource_type, "quarantined",
        criticality=criticality, compliant=False,
        missing_tags=missing,
        dry_run=DRY_RUN,
        extra={"status": "quarantined"},
    )

    if schedule_name and not DRY_RUN:
        _delete_schedule(schedule_name)

    put_governance_metric("quarantined", resource_type, criticality, dry_run=DRY_RUN)
    logger.info("resource=%s quarantined (SLA breached)", safe_id)
    return {"status": "quarantined", "resource_id": resource_id, "missing_tags": missing, "dry_run": DRY_RUN}


# ── Event parsing ─────────────────────────────────────────────────────────────


def _extract_event_fields(
    event: Dict[str, Any],
) -> tuple:
    """
    Handles both AWS Config compliance change events and CloudTrail create events.

    Config shape:  detail.configurationItem.{resourceId, resourceType, tags}
    CloudTrail shape: detail.requestParameters + detail.userIdentity.accountId
    """
    detail  = event.get("detail") or {}
    account_id = event.get("account", (detail.get("userIdentity") or {}).get("accountId", "unknown"))
    region  = event.get("region", "unknown")

    # Compliance-change events identify the resource but do not carry live tags.
    evaluation = detail.get("newEvaluationResult") or {}
    qualifier = (evaluation.get("evaluationResultIdentifier") or {}).get("evaluationResultQualifier") or {}
    if qualifier:
        resource_id = qualifier.get("resourceId", "")
        resource_type = _normalise_config_type(qualifier.get("resourceType", ""))
        tags = (_fetch_current_tags(resource_id, resource_type)
                if resource_id and resource_in_governance_scope(resource_id, resource_type) else [])
        return resource_id, resource_type, tags, region, account_id

    # Legacy configuration-item fixtures / configuration change events.
    config_item = detail.get("configurationItem") or detail.get("newEvaluationResult", {}).get("configurationItem", {})
    if config_item:
        resource_id   = config_item.get("resourceId", "")
        resource_type = _normalise_config_type(config_item.get("resourceType", ""))
        raw_tags      = config_item.get("tags", {})
        tags = [{"Key": k, "Value": v} for k, v in raw_tags.items()] if isinstance(raw_tags, dict) else (raw_tags or [])
        return resource_id, resource_type, tags, region, account_id

    # CloudTrail create event
    event_name     = detail.get("eventName", "")
    request_params = (detail.get("requestParameters") or {})
    resource_id    = _extract_cloudtrail_resource_id(event_name, request_params)
    resource_type  = _cloudtrail_resource_type(event_name)
    tags           = _extract_cloudtrail_tags(request_params)
    return resource_id, resource_type, tags, region, account_id


_CONFIG_TYPE_MAP = {
    "AWS::EC2::Instance":      "ec2",
    "AWS::S3::Bucket":         "s3",
    "AWS::RDS::DBInstance":    "rds",
    "AWS::Lambda::Function":   "lambda",
}


def _normalise_config_type(config_type: str) -> str:
    return _CONFIG_TYPE_MAP.get(config_type, config_type.split("::")[-1].lower())


def _cloudtrail_resource_type(event_name: str) -> str:
    return WATCHED_EVENTS.get(event_name, "unknown")


def _extract_cloudtrail_resource_id(event_name: str, params: Dict[str, Any]) -> str:
    if event_name == "RunInstances":
        instances = params.get("instancesSet", {}).get("items", [])
        return instances[0].get("instanceId", "") if instances else ""
    if event_name == "CreateBucket":
        return params.get("bucketName", "")
    if event_name == "CreateDBInstance":
        return params.get("dBInstanceIdentifier", "")
    if event_name == "CreateFunction20150331":
        return params.get("functionName", "")
    return ""


def _extract_cloudtrail_tags(params: Dict[str, Any]) -> List[Dict[str, str]]:
    tag_spec = params.get("tagSpecificationSet", {}).get("items", [])
    if tag_spec:
        return tag_spec[0].get("tags", {}).get("items", [])
    raw = params.get("tags", [])
    if isinstance(raw, dict):
        return [{"Key": k, "Value": v} for k, v in raw.items()]
    return raw or []


# ── Notifications ─────────────────────────────────────────────────────────────


def _send_alert(
    resource_id: str,
    resource_type: str,
    criticality: str,
    missing_tags: List[str],
    sla_hours: int,
    sla_deadline: str,
    account_id: str,
    region: str,
) -> None:
    if DRY_RUN:
        logger.info("[DRY_RUN] would notify resource=%s", sanitize_log_value(resource_id))
        return
    track  = "FAST" if criticality == "CRITICAL" else "SLOW"
    color  = SLACK_COLORS[track]
    emoji  = TRACK_EMOJI[track]
    subject = f"{emoji} [{criticality}] Untagged resource: {resource_id}"

    sns_body = (
        f"Resource {resource_id} ({resource_type}) in {account_id}/{region} "
        f"is missing required tags: {', '.join(missing_tags)}.\n"
        f"SLA: {sla_hours}h — deadline: {sla_deadline}"
    )
    sns_ok = send_sns(SNS_TOPIC_ARN, subject, sns_body)

    slack_payload = {
        "attachments": [{
            "color": color,
            "title": subject,
            "fields": [
                {"title": "Resource ID",    "value": resource_id,              "short": True},
                {"title": "Type",           "value": resource_type,            "short": True},
                {"title": "Account",        "value": account_id,               "short": True},
                {"title": "Region",         "value": region,                   "short": True},
                {"title": "Missing tags",   "value": ", ".join(missing_tags),  "short": False},
                {"title": "SLA deadline",   "value": sla_deadline,             "short": False},
            ],
            "footer": "Red Queen — Tagging Governance",
        }]
    }
    slack_ok = send_slack(SLACK_WEBHOOK, slack_payload)
    if (SNS_TOPIC_ARN and not sns_ok) or (SLACK_WEBHOOK and not slack_ok):
        raise RuntimeError("A configured notification channel failed")


# ── EventBridge Scheduler ─────────────────────────────────────────────────────

_SCHEDULE_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]")


def _schedule_name(resource_id: str) -> str:
    safe = _SCHEDULE_NAME_RE.sub("-", resource_id)
    if safe == resource_id and len(safe) <= 57:
        return f"rq-sla-{safe}"
    digest = hashlib.sha256(resource_id.encode("utf-8")).hexdigest()[:16]
    return f"rq-sla-{safe[:40]}-{digest}"


def _schedule_sla_check(
    resource_id: str,
    resource_type: str,
    criticality: str,
    sla_deadline: str,
    region: str,
) -> str:
    if not SCHEDULER_ROLE or not LAMBDA_ARN:
        raise RuntimeError("SCHEDULER_ROLE_ARN and AWS_LAMBDA_FUNCTION_ARN are required")

    name     = _schedule_name(resource_id)
    # EventBridge Scheduler uses "at()" for one-shot — format: at(yyyy-MM-ddTHH:mm:ss)
    # Round up, not down: an invocation before the deadline must not be lost.
    target_time = datetime.fromisoformat(sla_deadline).astimezone(timezone.utc)
    if target_time.microsecond:
        target_time += timedelta(seconds=1)
    at_time = target_time.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")

    payload = {
        "detail-type": "SLADeadline",
        "detail": {
            "resource_id":   resource_id,
            "resource_type": resource_type,
            "criticality":   criticality,
            "schedule_name": name,
            "sla_deadline":  sla_deadline,
        },
    }

    try:
        boto3.client("scheduler", region_name=region).create_schedule(
            Name=name,
            GroupName=SCHEDULER_GROUP,
            ScheduleExpression=f"at({at_time})",
            FlexibleTimeWindow={"Mode": "OFF"},
            Target={
                "Arn":     LAMBDA_ARN,
                "RoleArn": SCHEDULER_ROLE,
                "Input":   json.dumps(payload),
            },
            ActionAfterCompletion="DELETE",
        )
        logger.info("Scheduled SLA check: name=%s at=%s", name, at_time)
        return sla_deadline
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConflictException":
            raise
        schedule = boto3.client("scheduler", region_name=region).get_schedule(Name=name, GroupName=SCHEDULER_GROUP)
        previous = json.loads(schedule["Target"]["Input"])["detail"]
        if previous.get("resource_id") != resource_id or previous.get("resource_type") != resource_type:
            raise RuntimeError("Schedule identity conflict") from exc
        return previous["sla_deadline"]


def _delete_schedule(schedule_name: str) -> None:
    try:
        boto3.client("scheduler").delete_schedule(Name=schedule_name, GroupName=SCHEDULER_GROUP)
        logger.info("Deleted schedule: %s", schedule_name)
    except ClientError as exc:
        # ConflictException means it was already deleted (ActionAfterCompletion=DELETE race)
        logger.warning("Scheduler delete error (%s): %s", schedule_name, exc)


# ── Quarantine (tags only) ────────────────────────────────────────────────────


def _apply_quarantine_tags(resource_id: str, resource_type: str) -> None:
    """Tag the resource as quarantined. Never stops or deletes anything."""
    now = datetime.now(timezone.utc).isoformat()
    quarantine_tags = [
        {"Key": "Status",           "Value": "needs-review"},
        {"Key": "QuarantinedAt",    "Value": now},
        {"Key": "QuarantineReason", "Value": "missing_required_tags"},
    ]

    try:
        if resource_type == "ec2":
            boto3.client("ec2").create_tags(
                Resources=[resource_id],
                Tags=quarantine_tags,
            )
        elif resource_type == "s3":
            s3 = boto3.client("s3")
            try:
                existing = s3.get_bucket_tagging(Bucket=resource_id).get("TagSet", [])
            except ClientError as exc:
                if exc.response["Error"]["Code"] == "NoSuchTagSet":
                    existing = []
                else:
                    raise
            merged = {t["Key"]: t["Value"] for t in existing}
            merged.update({t["Key"]: t["Value"] for t in quarantine_tags})
            s3.put_bucket_tagging(
                Bucket=resource_id,
                Tagging={"TagSet": [{"Key": k, "Value": v} for k, v in merged.items()]},
            )
        elif resource_type == "rds":
            arn = _rds_arn(resource_id)
            if not arn:
                raise RuntimeError("RDS instance ARN is not available")
            if arn:
                boto3.client("rds").add_tags_to_resource(
                    ResourceName=arn,
                    Tags=quarantine_tags,
                )
        elif resource_type == "lambda":
            arn = _lambda_arn(resource_id)
            boto3.client("lambda").tag_resource(
                Resource=arn,
                Tags={t["Key"]: t["Value"] for t in quarantine_tags},
            )
        else:
            raise ValueError(f"Unsupported resource type: {resource_type}")
    except ClientError as exc:
        logger.error("quarantine tag error (%s): %s", sanitize_log_value(resource_id), exc)
        raise


def _rds_arn(db_identifier: str) -> Optional[str]:
    try:
        resp = boto3.client("rds").describe_db_instances(DBInstanceIdentifier=db_identifier)
        instances = resp.get("DBInstances", [])
        return instances[0].get("DBInstanceArn") if instances else None
    except ClientError as exc:
        logger.error("RDS describe error (%s): %s", sanitize_log_value(db_identifier), exc)
        raise


# ── Current tag fetch ─────────────────────────────────────────────────────────


def _fetch_current_tags(resource_id: str, resource_type: str) -> List[Dict[str, str]]:
    """Re-fetch live tags for SLA deadline compliance re-check."""
    try:
        if resource_type == "ec2":
            resp = boto3.client("ec2").describe_tags(
                Filters=[{"Name": "resource-id", "Values": [resource_id]}]
            )
            return [{"Key": t["Key"], "Value": t["Value"]} for t in resp.get("Tags", [])]

        if resource_type == "s3":
            try:
                resp = boto3.client("s3").get_bucket_tagging(Bucket=resource_id)
                return resp.get("TagSet", [])
            except ClientError as exc:
                if exc.response["Error"]["Code"] == "NoSuchTagSet":
                    return []
                raise

        if resource_type == "rds":
            arn = _rds_arn(resource_id)
            if not arn:
                raise RuntimeError("RDS instance ARN is not available")
            resp = boto3.client("rds").list_tags_for_resource(ResourceName=arn)
            return resp.get("TagList", [])

        if resource_type == "lambda":
            resp = boto3.client("lambda").list_tags(Resource=_lambda_arn(resource_id))
            raw = resp.get("Tags", {})
            return [{"Key": k, "Value": v} for k, v in raw.items()]

    except ClientError as exc:
        logger.error("_fetch_current_tags error (%s): %s", sanitize_log_value(resource_id), exc)
        raise

    return []


def _lambda_arn(resource_id: str) -> str:
    if resource_id.startswith("arn:"):
        return resource_id
    return boto3.client("lambda").get_function_configuration(FunctionName=resource_id)["FunctionArn"]
