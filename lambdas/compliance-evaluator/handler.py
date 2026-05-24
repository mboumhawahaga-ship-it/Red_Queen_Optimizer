"""
Compliance Evaluator Lambda — Phase 1 of Red Queen migration.

Triggered by:
  - EventBridge rule matching AWS Config NON_COMPLIANT changes
  - EventBridge Scheduler re-invoke at SLA deadline (detail-type = "SLADeadline")

Flow (initial trigger):
  1. Extract resource_id, resource_type, tags from event
  2. classify_resource → CRITICAL / NON_CRITICAL
  3. get_governance_record → skip if already 'handled'
  4. check_required_tags → if compliant, put record + return
  5. send_sns + send_slack (tiered SLA)
  6. put_governance_record(event=alerted, sla_deadline=now+SLA_HOURS)
  7. schedule EventBridge Scheduler one-shot at sla_deadline

Flow (SLA re-invoke):
  1. Re-check tags via AWS APIs
  2. If now compliant → put record(event=resolved) + delete scheduler
  3. If still non-compliant → apply quarantine tags + put record(event=quarantined)
"""

import os
import json
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

from ..shared.utils import (
    sanitize_resource_id,
    sanitize_log_value,
    check_required_tags,
    classify_resource,
    put_governance_record,
    get_governance_record,
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

    safe_id   = sanitize_log_value(resource_id)
    safe_type = sanitize_log_value(resource_type)

    # 1. classify
    criticality = classify_resource(resource_type, tags)
    logger.info("resource=%s type=%s criticality=%s", safe_id, safe_type, criticality)

    # 2. skip if already 'handled'
    existing = get_governance_record(TABLE_NAME, resource_id)
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
        logger.info("resource=%s is compliant", safe_id)
        return {"status": "compliant", "resource_id": resource_id}

    # 4. SLA
    sla_hours    = SLA_HOURS[criticality]
    now          = datetime.now(timezone.utc)
    sla_deadline = (now + timedelta(hours=sla_hours)).isoformat()
    environment  = tags_list_to_dict(tags).get("Environment", "unknown")

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

    # 7. schedule re-invoke
    if not DRY_RUN:
        _schedule_sla_check(resource_id, resource_type, criticality, sla_deadline, region)

    return {
        "status":       "alerted",
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

    safe_id = sanitize_log_value(resource_id)

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

    logger.info("resource=%s quarantined (SLA breached)", safe_id)
    return {"status": "quarantined", "resource_id": resource_id, "missing_tags": missing}


# ── Event parsing ─────────────────────────────────────────────────────────────


def _extract_event_fields(
    event: Dict[str, Any],
) -> tuple:
    """
    Handles both AWS Config compliance change events and CloudTrail create events.

    Config shape:  detail.configurationItem.{resourceId, resourceType, tags}
    CloudTrail shape: detail.requestParameters + detail.userIdentity.accountId
    """
    detail  = event.get("detail", {})
    account_id = event.get("account", detail.get("userIdentity", {}).get("accountId", "unknown"))
    region  = event.get("region", "unknown")

    # AWS Config NON_COMPLIANT
    config_item = detail.get("configurationItem") or detail.get("newEvaluationResult", {}).get("configurationItem", {})
    if config_item:
        resource_id   = config_item.get("resourceId", "")
        resource_type = _normalise_config_type(config_item.get("resourceType", ""))
        raw_tags      = config_item.get("tags", {})
        tags = [{"Key": k, "Value": v} for k, v in raw_tags.items()] if isinstance(raw_tags, dict) else (raw_tags or [])
        return resource_id, resource_type, tags, region, account_id

    # CloudTrail create event
    event_name     = detail.get("eventName", "")
    request_params = detail.get("requestParameters", {})
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
    track  = "FAST" if criticality == "CRITICAL" else "SLOW"
    color  = SLACK_COLORS[track]
    emoji  = TRACK_EMOJI[track]
    subject = f"{emoji} [{criticality}] Untagged resource: {resource_id}"

    sns_body = (
        f"Resource {resource_id} ({resource_type}) in {account_id}/{region} "
        f"is missing required tags: {', '.join(missing_tags)}.\n"
        f"SLA: {sla_hours}h — deadline: {sla_deadline}"
    )
    send_sns(SNS_TOPIC_ARN, subject, sns_body)

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
    send_slack(SLACK_WEBHOOK, slack_payload)


# ── EventBridge Scheduler ─────────────────────────────────────────────────────

_SCHEDULE_NAME_RE = re.compile(r"[^a-zA-Z0-9_-]")


def _schedule_name(resource_id: str) -> str:
    safe = _SCHEDULE_NAME_RE.sub("-", resource_id)[:64]
    return f"rq-sla-{safe}"


def _schedule_sla_check(
    resource_id: str,
    resource_type: str,
    criticality: str,
    sla_deadline: str,
    region: str,
) -> None:
    if not SCHEDULER_ROLE or not LAMBDA_ARN:
        logger.warning("SCHEDULER_ROLE_ARN or AWS_LAMBDA_FUNCTION_ARN not set — schedule skipped")
        return

    name     = _schedule_name(resource_id)
    # EventBridge Scheduler uses "at()" for one-shot — format: at(yyyy-MM-ddTHH:mm:ss)
    at_time  = sla_deadline[:19]  # trim microseconds/tz to bare ISO

    payload = {
        "detail-type": "SLADeadline",
        "detail": {
            "resource_id":   resource_id,
            "resource_type": resource_type,
            "criticality":   criticality,
            "schedule_name": name,
        },
    }

    try:
        boto3.client("scheduler", region_name=region).create_schedule(
            Name=name,
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
    except ClientError as exc:
        logger.error("Scheduler create error (%s): %s", resource_id, exc)


def _delete_schedule(schedule_name: str) -> None:
    try:
        boto3.client("scheduler").delete_schedule(Name=schedule_name)
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
            if arn:
                boto3.client("rds").add_tags_to_resource(
                    ResourceName=arn,
                    Tags=quarantine_tags,
                )
        elif resource_type == "lambda":
            boto3.client("lambda").tag_resource(
                Resource=resource_id,
                Tags={t["Key"]: t["Value"] for t in quarantine_tags},
            )
        else:
            logger.warning("_apply_quarantine_tags: unsupported type=%s", resource_type)
    except ClientError as exc:
        logger.error("quarantine tag error (%s): %s", sanitize_log_value(resource_id), exc)


def _rds_arn(db_identifier: str) -> Optional[str]:
    try:
        resp = boto3.client("rds").describe_db_instances(DBInstanceIdentifier=db_identifier)
        instances = resp.get("DBInstances", [])
        return instances[0].get("DBInstanceArn") if instances else None
    except ClientError as exc:
        logger.error("RDS describe error (%s): %s", sanitize_log_value(db_identifier), exc)
        return None


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
                return []
            resp = boto3.client("rds").list_tags_for_resource(ResourceName=arn)
            return resp.get("TagList", [])

        if resource_type == "lambda":
            resp = boto3.client("lambda").list_tags(Resource=resource_id)
            raw = resp.get("Tags", {})
            return [{"Key": k, "Value": v} for k, v in raw.items()]

    except ClientError as exc:
        logger.error("_fetch_current_tags error (%s): %s", sanitize_log_value(resource_id), exc)

    return []
