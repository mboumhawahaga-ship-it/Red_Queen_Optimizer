"""
Shared utilities for all Red Queen Lambda functions.

Covers:
  - Input sanitization  (sanitize_resource_id)
  - Tag helpers         (check_required_tags, tags_list_to_dict, tags_dict_to_list)
  - Classification      (classify_resource)
  - DynamoDB helpers    (put_governance_record, get_governance_record)
  - Notifications       (send_sns, send_slack)
  - Metrics             (put_governance_metric)
"""

import re
import os
import fnmatch
import time
import json
import logging
import urllib.request
import urllib.error
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Optional, Any

import boto3

from .constants import (
    REQUIRED_TAGS,
    PLACEHOLDER_TAG_VALUES,
    CRITICAL_RESOURCE_TYPES,
    TTL_DAYS,
    METRICS_NAMESPACE,
    METRIC_GOVERNANCE_EVENTS,
    METRIC_OUTCOMES,
)

logger = logging.getLogger(__name__)


def resource_in_governance_scope(resource_id: str, resource_type: str) -> bool:
    """Skip unlisted workloads before AWS calls; IAM remains the authority.

    Missing configuration preserves compatibility with old local callers.
    Terraform always supplies the allow-list, including an empty one.
    """
    configured = os.environ.get("GOVERNANCE_SCOPE")
    if configured is None:
        return True
    scope = json.loads(configured)
    region = os.environ.get("GOVERNANCE_REGION", "")
    account = os.environ.get("GOVERNANCE_ACCOUNT_ID", "")
    formats = {
        "ec2": ("ec2_instance_arns", f"arn:aws:ec2:{region}:{account}:instance/{resource_id}"),
        "s3": ("s3_bucket_arns", f"arn:aws:s3:::{resource_id}"),
        "rds": ("rds_db_arns", f"arn:aws:rds:{region}:{account}:db:{resource_id}"),
        "lambda": ("lambda_function_arns", f"arn:aws:lambda:{region}:{account}:function:{resource_id}"),
    }
    if resource_type.lower() not in formats:
        return False
    key, derived_arn = formats[resource_type.lower()]
    arn = resource_id if resource_id.startswith("arn:") else derived_arn
    return any(fnmatch.fnmatchcase(arn, pattern) for pattern in scope.get(key, []))


# ── Input sanitization ────────────────────────────────────────────────────────

# Covers: EC2 instance IDs, ARNs, S3 bucket names, Lambda function names,
# RDS identifiers — all valid AWS resource ID formats.
_RESOURCE_ID_RE = re.compile(r"^[\w:/.\\-]{1,512}$")


def sanitize_resource_id(resource_id: str) -> str:
    """
    Validate and return resource_id.
    Raises ValueError if the value could be used for NoSQL injection,
    log injection, or path traversal (characters outside the allowed
    AWS ID charset, or '..' sequences).
    """
    if not resource_id or not _RESOURCE_ID_RE.match(resource_id):
        raise ValueError(f"Invalid resource_id: {resource_id!r}")
    if ".." in resource_id:
        raise ValueError(f"Invalid resource_id (path traversal): {resource_id!r}")
    return resource_id


def sanitize_log_value(value: str) -> str:
    """Strip CR/LF/TAB from a value before writing it to logs."""
    return value.replace("\r", "").replace("\n", "").replace("\t", " ")

# ── Tag helpers ───────────────────────────────────────────────────────────────


def tags_list_to_dict(tags: Optional[List[Dict[str, str]]]) -> Dict[str, str]:
    """Convert [{'Key': k, 'Value': v}, ...] → {k: v}."""
    if not tags:
        return {}
    return {t.get("Key", ""): t.get("Value", "") for t in tags}


def tags_dict_to_list(tags: Dict[str, str]) -> List[Dict[str, str]]:
    """Convert {k: v} → [{'Key': k, 'Value': v}, ...]."""
    return [{"Key": k, "Value": v} for k, v in tags.items()]


def is_placeholder_value(value: Optional[str]) -> bool:
    """Reject missing, non-string and known filler values."""
    return not isinstance(value, str) or value.strip().lower() in PLACEHOLDER_TAG_VALUES


def check_required_tags(tags: List[Dict[str, str]]) -> Tuple[bool, List[str]]:
    """
    Return (is_compliant, missing_tag_names).
    """
    values = tags_list_to_dict(tags)
    missing = [t for t in REQUIRED_TAGS if is_placeholder_value(values.get(t))]
    return len(missing) == 0, missing

# ── Classification ────────────────────────────────────────────────────────────


def classify_resource(resource_type: str, tags: List[Dict[str, str]]) -> str:
    """
    Return 'CRITICAL' or 'NON_CRITICAL'.

    Rules (in priority order):
      1. CriticalWorkload=true tag           → CRITICAL
      2. resource_type in CRITICAL_TYPES     → CRITICAL  (rds)
      3. EC2 with Environment=prod           → CRITICAL
      4. Everything else                     → NON_CRITICAL
    """
    tag_map = tags_list_to_dict(tags)

    if tag_map.get("CriticalWorkload", "").lower() == "true":
        return "CRITICAL"
    if resource_type.lower() in CRITICAL_RESOURCE_TYPES:
        return "CRITICAL"
    if resource_type.lower() == "ec2" and tag_map.get("Environment") == "prod":
        return "CRITICAL"
    return "NON_CRITICAL"

# ── DynamoDB helpers ──────────────────────────────────────────────────────────


def _ttl_expiry() -> int:
    return int(time.time()) + (TTL_DAYS * 24 * 3600)


def put_governance_record(
    table_name: str,
    resource_id: str,
    resource_type: str,
    event: str,
    *,
    criticality: str = "UNKNOWN",
    compliant: bool = False,
    missing_tags: Optional[List[str]] = None,
    environment: str = "unknown",
    dry_run: bool = True,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Write a governance event record to DynamoDB.
    Single persistence function shared by all Lambdas — identical schema everywhere.
    """
    if dry_run:
        logger.info("[DRY_RUN] governance event=%s resource=%s", event, sanitize_log_value(resource_id))
        return
    if not table_name:
        return
    try:
        resource_id = sanitize_resource_id(resource_id)
    except ValueError as exc:
        logger.warning("put_governance_record skipped — %s", exc)
        return

    item: Dict[str, Any] = {
        "resource_id":    resource_id,
        "scan_timestamp": datetime.now(timezone.utc).isoformat(),
        "resource_type":  resource_type,
        "event":          event,
        "criticality":    criticality,
        "compliant":      compliant,
        "missing_tags":   missing_tags or [],
        "environment":    environment,
        "dry_run":        dry_run,
        "ttl_expiry":     _ttl_expiry(),
    }
    if extra:
        item.update(extra)

    try:
        boto3.resource("dynamodb").Table(table_name).put_item(Item=item)
    except Exception as exc:
        logger.warning("DynamoDB write error (%s): %s", resource_id, exc)
        raise


def get_governance_record(
    table_name: str,
    resource_id: str,
    event_filter: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Return the most recent DynamoDB record for resource_id.
    Optionally filter by event type (e.g. 'scan', 'remediation').
    Returns None if not found.

    Uses ScanIndexForward=False to get the latest record first,
    then applies event_filter via FilterExpression server-side.
    """
    if not table_name:
        return None
    try:
        resource_id = sanitize_resource_id(resource_id)
    except ValueError as exc:
        logger.warning("get_governance_record skipped — %s", exc)
        return None

    try:
        table = boto3.resource("dynamodb").Table(table_name)

        kwargs: Dict[str, Any] = {
            "KeyConditionExpression": "resource_id = :rid",
            "ExpressionAttributeValues": {":rid": resource_id},
            "ScanIndexForward": False,
            "Limit": 1,
            "ConsistentRead": True,
        }
        if event_filter:
            kwargs["FilterExpression"] = "#ev = :ev"
            kwargs["ExpressionAttributeNames"] = {"#ev": "event"}
            kwargs["ExpressionAttributeValues"][":ev"] = event_filter
            kwargs["Limit"] = 20  # filter needs a wider window

        while True:
            resp = table.query(**kwargs)
            items = resp.get("Items", [])
            if items:
                return items[0]
            if not resp.get("LastEvaluatedKey"):
                return None
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    except Exception as exc:
        logger.warning("DynamoDB read error (%s): %s", resource_id, exc)
        raise

# ── Notifications ─────────────────────────────────────────────────────────────


def send_sns(topic_arn: str, subject: str, message: str) -> bool:
    """
    Publish a message to SNS. Returns True on success.
    """
    if not topic_arn:
        logger.warning("SNS_TOPIC_ARN not configured — email skipped")
        return False
    try:
        boto3.client("sns").publish(
            TopicArn=topic_arn,
            Subject=subject[:100],   # SNS subject max 100 chars
            Message=message,
        )
        logger.info("SNS published: %s", subject)
        return True
    except Exception as exc:
        logger.error("SNS error: %s", exc)
        return False


def send_slack(webhook_url: str, payload: Dict[str, Any]) -> bool:
    """
    POST a Slack webhook payload. Returns True on success.

    An explicit https:// guard before urllib.urlopen prevents SSRF via
    file:// or custom schemes.
    """
    if not webhook_url:
        logger.info("SLACK_WEBHOOK_URL not configured — Slack skipped")
        return False

    if not webhook_url.startswith("https://"):
        logger.warning("SLACK_WEBHOOK_URL must start with https:// — Slack skipped")
        return False

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            logger.info("Slack sent — status=%d", resp.status)
        return True
    except urllib.error.URLError as exc:
        logger.error("Slack error: %s", exc)
        return False

# ── CloudWatch metrics ────────────────────────────────────────────────────────


def put_governance_metric(outcome: str, resource_type: str, criticality: str = "UNKNOWN", *,
                          dry_run: bool = False) -> bool:
    """
    Publish one GovernanceEvents datapoint (value=1) to CloudWatch.

    Two datapoints are written per call because CloudWatch dimensions are an
    exact match — a metric with {Outcome} cannot be aggregated from metrics
    with {Outcome, ResourceType}:
      - {Outcome}                              → global counters
      - {Outcome, ResourceType, Criticality}   → breakdown panels

    Dry runs only log intent. Returns True on success; never raises.
    """
    if dry_run:
        logger.info("[DRY_RUN] metric outcome=%s type=%s", outcome, resource_type)
        return False
    if outcome not in METRIC_OUTCOMES:
        logger.warning("put_governance_metric: unknown outcome %r — skipped", outcome)
        return False

    now = datetime.now(timezone.utc)
    base = {
        "MetricName": METRIC_GOVERNANCE_EVENTS,
        "Timestamp": now,
        "Value": 1,
        "Unit": "Count",
    }
    metric_data = [
        {**base, "Dimensions": [{"Name": "Outcome", "Value": outcome}]},
        {**base, "Dimensions": [
            {"Name": "Outcome",       "Value": outcome},
            {"Name": "ResourceType",  "Value": resource_type or "unknown"},
            {"Name": "Criticality",   "Value": criticality or "UNKNOWN"},
        ]},
    ]

    try:
        boto3.client("cloudwatch").put_metric_data(
            Namespace=METRICS_NAMESPACE,
            MetricData=metric_data,
        )
        return True
    except Exception as exc:
        logger.warning("CloudWatch metric error (%s/%s): %s", outcome, resource_type, exc)
        return False
