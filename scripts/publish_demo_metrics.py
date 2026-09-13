"""
Publish a handful of demo GovernanceEvents datapoints so the Grafana dashboard
has something to display before real Config / CloudTrail events arrive.

Uses the exact same helper as the compliance-evaluator Lambda, so what you see
on the dashboard is what production would publish.

Usage:
    python scripts/publish_demo_metrics.py            # 12 events, eu-west-1
    AWS_DEFAULT_REGION=us-east-1 python scripts/publish_demo_metrics.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lambdas.shared.utils import put_governance_metric  # noqa: E402

DEMO_EVENTS = [
    ("compliant",   "ec2",    "NON_CRITICAL"),
    ("compliant",   "s3",     "NON_CRITICAL"),
    ("compliant",   "lambda", "NON_CRITICAL"),
    ("alerted",     "ec2",    "NON_CRITICAL"),
    ("alerted",     "ec2",    "CRITICAL"),
    ("alerted",     "s3",     "NON_CRITICAL"),
    ("alerted",     "rds",    "CRITICAL"),
    ("resolved",    "ec2",    "NON_CRITICAL"),
    ("resolved",    "rds",    "CRITICAL"),
    ("quarantined", "s3",     "NON_CRITICAL"),
    ("quarantined", "ec2",    "CRITICAL"),
    ("quarantined", "lambda", "NON_CRITICAL"),
]

if __name__ == "__main__":
    ok = sum(put_governance_metric(*e) for e in DEMO_EVENTS)
    region = os.environ.get("AWS_DEFAULT_REGION", "default region")
    print(f"{ok}/{len(DEMO_EVENTS)} demo events published to RedQueen/Governance ({region})")
    print("Metrics take 1-2 minutes to appear in CloudWatch / Grafana.")
