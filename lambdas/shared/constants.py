"""
Shared constants for all Red Queen Lambda functions.
Single source of truth — change here, applies everywhere.
"""

from typing import Dict, Set, List

# ── Tags ──────────────────────────────────────────────────────────────────────

REQUIRED_TAGS: List[str] = ["Owner", "Squad", "CostCenter", "Environment"]

# ── Classification ────────────────────────────────────────────────────────────

# Resource types that are always CRITICAL regardless of environment
CRITICAL_RESOURCE_TYPES: Set[str] = {"rds"}

# ── SLA ───────────────────────────────────────────────────────────────────────

SLA_HOURS: Dict[str, int] = {
    "CRITICAL":     36,
    "NON_CRITICAL": 168,   # 7 days
}

# ── Governance statuses (DynamoDB) ────────────────────────────────────────────

# alerted     → notification sent, owner has until sla_deadline
# resolved    → owner added required tags, resource is now compliant
# handled     → owner marked resource as intentional (excluded from future evaluations)
# quarantined → SLA expired, resource has been stopped/throttled/blocked
VALID_STATUSES: Set[str] = {"alerted", "resolved", "handled", "quarantined"}

# ── Feedback actions ──────────────────────────────────────────────────────────

VALID_ACTIONS: Set[str] = {"approve", "reject", "tag"}

# ── DynamoDB ──────────────────────────────────────────────────────────────────

TTL_DAYS: int = 90

# ── CloudTrail events watched by auto-tagger ─────────────────────────────────

WATCHED_EVENTS: Dict[str, str] = {
    "RunInstances":             "ec2",
    "CreateBucket":             "s3",
    "CreateDBInstance":         "rds",
    "CreateFunction20150331":   "lambda",
}

# ── Notification formatting ───────────────────────────────────────────────────

SLACK_COLORS: Dict[str, str] = {
    "FAST":                "#f39c12",   # orange — attention
    "SLOW":                "#e74c3c",   # red    — urgent
    "SLOW_ESCALATION":     "#8e44ad",   # purple — escalation
    "SLOW_FINAL_REMINDER": "#2c3e50",   # black  — final warning
}

TRACK_EMOJI: Dict[str, str] = {
    "FAST":                "⚠️",
    "SLOW":                "🔴",
    "SLOW_ESCALATION":     "🚨",
    "SLOW_FINAL_REMINDER": "🆘",
}
