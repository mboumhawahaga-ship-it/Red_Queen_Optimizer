#!/usr/bin/env python3
"""Reproducible, offline benchmark of Red Queen's real Lambda handlers."""

import argparse
import csv
import importlib.util
import json
import logging
import os
import platform
import socket
import statistics
import sys
import time
import types
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SIZES = (200, 1000)
RESOURCE_TYPES = ("ec2", "s3", "rds", "lambda")
ACCOUNT_ID = "123456789012"
REGION = "eu-west-1"
TABLE_NAME = "red-queen-benchmark"
SCOPE = {
    "ec2_instance_arns": [f"arn:aws:ec2:{REGION}:{ACCOUNT_ID}:instance/i-bench-*"],
    "s3_bucket_arns": ["arn:aws:s3:::rq-bench-*"],
    "rds_db_arns": [f"arn:aws:rds:{REGION}:{ACCOUNT_ID}:db:rq-bench-*"],
    "lambda_function_arns": [
        f"arn:aws:lambda:{REGION}:{ACCOUNT_ID}:function:rq-bench-*"
    ],
}
WRITE_METHODS = {
    "ec2.create_tags",
    "s3.put_bucket_tagging",
    "rds.add_tags_to_resource",
    "lambda.tag_resource",
    "dynamodb.put_item",
    "scheduler.create_schedule",
    "scheduler.delete_schedule",
    "sns.publish",
    "cloudwatch.put_metric_data",
}


class InMemoryTable:
    def __init__(self, backend):
        self.backend = backend

    def put_item(self, *, Item):
        self.backend.calls["dynamodb.put_item"] += 1
        self.backend.records[Item["resource_id"]].append(dict(Item))
        return {}

    def query(self, **kwargs):
        self.backend.calls["dynamodb.query"] += 1
        values = kwargs["ExpressionAttributeValues"]
        resource_id = values[":rid"]
        records = list(self.backend.records.get(resource_id, []))
        event_filter = values.get(":ev")
        if event_filter:
            records = [item for item in records if item.get("event") == event_filter]
        records.sort(key=lambda item: item["scan_timestamp"], reverse=True)
        return {"Items": records[: kwargs.get("Limit", 1)]}


class InMemoryBackend:
    """Small stateful AWS API double; handlers and business helpers stay real."""

    def __init__(self):
        self.tags = {resource_type: {} for resource_type in RESOURCE_TYPES}
        self.records = defaultdict(list)
        self.calls = Counter()
        self.schedules = {}

    def _record(self, service, method):
        self.calls[f"{service}.{method}"] += 1

    def _tags_list(self, resource_type, resource_id):
        return [
            {"Key": key, "Value": value}
            for key, value in self.tags[resource_type].get(resource_id, {}).items()
        ]

    def _merge_tags(self, resource_type, resource_id, tags):
        current = self.tags[resource_type].setdefault(resource_id, {})
        current.update({tag["Key"]: tag["Value"] for tag in tags})

    def client(self, service_name, **kwargs):
        return FakeClient(self, service_name)

    def resource(self, service_name, **kwargs):
        self._record(service_name, "resource")
        if service_name != "dynamodb":
            raise AssertionError(f"Unexpected boto3.resource call: {service_name}")
        return FakeDynamoResource(self)

    def write_count(self):
        return sum(self.calls[name] for name in WRITE_METHODS)


class FakeDynamoResource:
    def __init__(self, backend):
        self.backend = backend

    def Table(self, name):
        self.backend.calls["dynamodb.Table"] += 1
        return InMemoryTable(self.backend)


class FakeClient:
    def __init__(self, backend, service):
        self.backend = backend
        self.service = service

    def describe_instances(self, *, InstanceIds):
        self.backend._record("ec2", "describe_instances")
        resource_id = InstanceIds[0]
        return {"Reservations": [{"Instances": [{"Tags": self.backend._tags_list("ec2", resource_id)}]}]}

    def create_tags(self, *, Resources, Tags):
        self.backend._record("ec2", "create_tags")
        for resource_id in Resources:
            self.backend._merge_tags("ec2", resource_id, Tags)
        return {}

    def describe_tags(self, *, Filters):
        self.backend._record("ec2", "describe_tags")
        resource_id = Filters[0]["Values"][0]
        return {
            "Tags": [
                {"Key": key, "Value": value}
                for key, value in self.backend.tags["ec2"].get(resource_id, {}).items()
            ]
        }

    def get_bucket_tagging(self, *, Bucket):
        self.backend._record("s3", "get_bucket_tagging")
        return {"TagSet": self.backend._tags_list("s3", Bucket)}

    def put_bucket_tagging(self, *, Bucket, Tagging):
        self.backend._record("s3", "put_bucket_tagging")
        self.backend.tags["s3"][Bucket] = {
            item["Key"]: item["Value"] for item in Tagging.get("TagSet", [])
        }
        return {}

    def describe_db_instances(self, *, DBInstanceIdentifier):
        self.backend._record("rds", "describe_db_instances")
        return {
            "DBInstances": [{
                "DBInstanceArn": (
                    f"arn:aws:rds:{REGION}:{ACCOUNT_ID}:db:{DBInstanceIdentifier}"
                )
            }]
        }

    def list_tags_for_resource(self, *, ResourceName):
        self.backend._record("rds", "list_tags_for_resource")
        resource_id = ResourceName.rsplit(":", 1)[-1]
        return {"TagList": self.backend._tags_list("rds", resource_id)}

    def add_tags_to_resource(self, *, ResourceName, Tags):
        self.backend._record("rds", "add_tags_to_resource")
        resource_id = ResourceName.rsplit(":", 1)[-1]
        self.backend._merge_tags("rds", resource_id, Tags)
        return {}

    def get_function_configuration(self, *, FunctionName):
        self.backend._record("lambda", "get_function_configuration")
        return {"FunctionArn": self._lambda_arn(FunctionName)}

    def list_tags(self, *, Resource):
        self.backend._record("lambda", "list_tags")
        resource_id = Resource.rsplit(":", 1)[-1]
        return {"Tags": dict(self.backend.tags["lambda"].get(resource_id, {}))}

    def tag_resource(self, *, Resource, Tags):
        self.backend._record("lambda", "tag_resource")
        resource_id = Resource.rsplit(":", 1)[-1]
        self.backend.tags["lambda"].setdefault(resource_id, {}).update(Tags)
        return {}

    @staticmethod
    def _lambda_arn(function_name):
        return f"arn:aws:lambda:{REGION}:{ACCOUNT_ID}:function:{function_name}"

    def create_schedule(self, *, Name, **kwargs):
        self.backend._record("scheduler", "create_schedule")
        self.backend.schedules[Name] = kwargs
        return {"ScheduleArn": f"arn:aws:scheduler:{REGION}:{ACCOUNT_ID}:schedule/{Name}"}

    def get_schedule(self, *, Name, **kwargs):
        self.backend._record("scheduler", "get_schedule")
        return self.backend.schedules[Name]

    def delete_schedule(self, *, Name, **kwargs):
        self.backend._record("scheduler", "delete_schedule")
        self.backend.schedules.pop(Name, None)
        return {}

    def publish(self, **kwargs):
        self.backend._record("sns", "publish")
        return {"MessageId": "mock-message"}

    def put_metric_data(self, **kwargs):
        self.backend._record("cloudwatch", "put_metric_data")
        return {}


class Harness:
    def __init__(self, backend, modules):
        self.backend = backend
        self.modules = modules
        self.activate()

    def activate(self):
        self.modules.tagger.ec2_client = self.backend.client("ec2")
        self.modules.tagger.s3_client = self.backend.client("s3")
        self.modules.tagger.rds_client = self.backend.client("rds")
        self.modules.tagger.lambda_client = self.backend.client("lambda")


def _load_real_modules(active_backend):
    os.environ.update({
        "AWS_ACCESS_KEY_ID": "benchmark-only",
        "AWS_SECRET_ACCESS_KEY": "benchmark-only",
        "AWS_SESSION_TOKEN": "benchmark-only",
        "AWS_DEFAULT_REGION": REGION,
        "AWS_EC2_METADATA_DISABLED": "true",
        "GOVERNANCE_REGION": REGION,
        "GOVERNANCE_ACCOUNT_ID": ACCOUNT_ID,
        "GOVERNANCE_SCOPE": json.dumps(SCOPE),
        "GOVERNANCE_TABLE": TABLE_NAME,
        "DRY_RUN": "false",
        "DEFAULT_OWNER": "benchmark-owner@example.test",
        "DEFAULT_SQUAD": "platform",
        "DEFAULT_COST_CENTER": "CC-BENCH",
        "DEFAULT_ENVIRONMENT": "test",
        "SNS_TOPIC_ARN": "",
        "SLACK_WEBHOOK_URL": "",
        "SCHEDULER_GROUP_NAME": "benchmark-sla",
        "SCHEDULER_ROLE_ARN": f"arn:aws:iam::{ACCOUNT_ID}:role/benchmark",
        "AWS_LAMBDA_FUNCTION_ARN": (
            f"arn:aws:lambda:{REGION}:{ACCOUNT_ID}:function:benchmark-evaluator"
        ),
    })

    import boto3

    def client_factory(service_name, **kwargs):
        return active_backend["value"].client(service_name, **kwargs)

    def resource_factory(service_name, **kwargs):
        return active_backend["value"].resource(service_name, **kwargs)

    root_package = types.ModuleType("rq_local_benchmark")
    root_package.__path__ = []
    sys.modules[root_package.__name__] = root_package
    for package in ("shared", "auto_tagger", "evaluator", "feedback"):
        module = types.ModuleType(f"rq_local_benchmark.{package}")
        module.__path__ = []
        sys.modules[module.__name__] = module

    paths = {
        "shared.constants": ROOT / "lambdas" / "shared" / "constants.py",
        "shared.utils": ROOT / "lambdas" / "shared" / "utils.py",
        "auto_tagger.handler": ROOT / "lambdas" / "auto-tagger" / "handler.py",
        "evaluator.handler": ROOT / "lambdas" / "compliance-evaluator" / "handler.py",
        "feedback.handler": ROOT / "lambdas" / "feedback-api" / "handler.py",
    }
    loaded = {}
    with patch.object(boto3, "client", side_effect=client_factory), patch.object(
        boto3, "resource", side_effect=resource_factory
    ):
        for name, path in paths.items():
            module_name = f"rq_local_benchmark.{name}"
            spec = importlib.util.spec_from_file_location(module_name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            loaded[name] = module

        modules = types.SimpleNamespace(
            utils=loaded["shared.utils"],
            tagger=loaded["auto_tagger.handler"],
            evaluator=loaded["evaluator.handler"],
            feedback=loaded["feedback.handler"],
        )
        for module in (modules.tagger, modules.evaluator, modules.feedback):
            module.TABLE_NAME = TABLE_NAME
        modules.tagger.DEFAULT_OWNER = "benchmark-owner@example.test"
        modules.tagger.DEFAULT_SQUAD = "platform"
        modules.tagger.DEFAULT_COST_CENTER = "CC-BENCH"
        modules.tagger.DEFAULT_ENVIRONMENT = "test"
        modules.evaluator.SNS_TOPIC_ARN = ""
        modules.evaluator.SLACK_WEBHOOK = ""
        modules.evaluator.SCHEDULER_ROLE = (
            f"arn:aws:iam::{ACCOUNT_ID}:role/benchmark"
        )
        modules.evaluator.LAMBDA_ARN = (
            f"arn:aws:lambda:{REGION}:{ACCOUNT_ID}:function:benchmark-evaluator"
        )
        for module in (modules.tagger, modules.evaluator, modules.utils):
            module.logger.setLevel(logging.CRITICAL)
        return modules, boto3, client_factory, resource_factory


def _resource_id(resource_type, index):
    suffix = f"{index:06d}"
    if resource_type == "ec2":
        return f"i-bench-{suffix}"
    if resource_type == "s3":
        return f"rq-bench-s3-{suffix}"
    if resource_type == "rds":
        return f"rq-bench-rds-{suffix}"
    return f"rq-bench-lambda-{suffix}"


def _creation_event(resource_type, resource_id):
    detail = {
        "eventName": {
            "ec2": "RunInstances",
            "s3": "CreateBucket",
            "rds": "CreateDBInstance",
            "lambda": "CreateFunction20150331",
        }[resource_type],
        "requestParameters": {},
        "responseElements": {},
        "userIdentity": {"arn": f"arn:aws:iam::{ACCOUNT_ID}:user/benchmark@example.test"},
    }
    if resource_type == "ec2":
        detail["responseElements"] = {
            "instancesSet": {"items": [{"instanceId": resource_id}]}
        }
    elif resource_type == "s3":
        detail["requestParameters"] = {"bucketName": resource_id}
    elif resource_type == "rds":
        detail["requestParameters"] = {"dBInstanceIdentifier": resource_id}
    else:
        detail["requestParameters"] = {"functionName": resource_id}
        detail["responseElements"] = {
            "functionArn": (
                f"arn:aws:lambda:{REGION}:{ACCOUNT_ID}:function:{resource_id}"
            )
        }
    return {"detail": detail}


def _seed_resource(backend, resource_type, resource_id):
    backend.tags[resource_type][resource_id] = (
        {"Project": "red-queen"} if resource_type == "s3" else {}
    )


def _extract_result(response, resource_type):
    if response.get("statusCode") != 200:
        raise AssertionError(f"Unexpected statusCode: {response.get('statusCode')}")
    body = json.loads(response["body"])
    results = body.get(resource_type)
    if not results or len(results) != 1:
        raise AssertionError(f"Expected one {resource_type} result: {body}")
    return results[0]


def _percentile(values, percentile):
    """Inclusive linear interpolation, equivalent to common PERCENTILE.INC."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _event_plan(size):
    plan = []
    for index in range(size):
        resource_type = RESOURCE_TYPES[index % len(RESOURCE_TYPES)]
        plan.append((resource_type, _resource_id(resource_type, index)))
    return plan


def _run_warmup(modules, active_backend):
    backend = InMemoryBackend()
    active_backend["value"] = backend
    harness = Harness(backend, modules)
    for resource_type in RESOURCE_TYPES:
        resource_id = _resource_id(resource_type, 900000 + RESOURCE_TYPES.index(resource_type))
        _seed_resource(backend, resource_type, resource_id)
        response = modules.tagger.lambda_handler(
            _creation_event(resource_type, resource_id), None
        )
        result = _extract_result(response, resource_type)
        if result.get("status") != "tagged":
            raise RuntimeError(f"Warm-up failed for {resource_type}: {result}")
    return harness


def _run_scenario(size, repetition, modules, active_backend):
    backend = InMemoryBackend()
    active_backend["value"] = backend
    Harness(backend, modules)
    modules.tagger.DRY_RUN = False

    plan = _event_plan(size)
    for resource_type, resource_id in plan:
        _seed_resource(backend, resource_type, resource_id)
    events = [
        (resource_type, resource_id, _creation_event(resource_type, resource_id))
        for resource_type, resource_id in plan
    ]

    rows = []
    elapsed_start = time.perf_counter_ns()
    for index, (resource_type, resource_id, event) in enumerate(events):
        event_start = time.perf_counter_ns()
        error = ""
        status = ""
        try:
            response = modules.tagger.lambda_handler(event, None)
            result = _extract_result(response, resource_type)
            status = result.get("status", "")
            success = status == "tagged" and not result.get("tags_unresolved")
            if not success:
                error = f"unexpected_result:{json.dumps(result, sort_keys=True)}"
        except Exception as exc:  # Retain and report all per-event failures.
            success = False
            error = f"{type(exc).__name__}: {exc}"
        latency_ns = time.perf_counter_ns() - event_start
        rows.append({
            "scenario_events": size,
            "repetition": repetition,
            "event_index": index + 1,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "latency_ms": latency_ns / 1_000_000,
            "success": success,
            "status": status,
            "error": error,
        })
    elapsed_ns = time.perf_counter_ns() - elapsed_start

    missing_or_bad = []
    preserved = 0
    for resource_type, resource_id in plan:
        tags = backend.tags[resource_type].get(resource_id, {})
        required = ("Owner", "Squad", "CostCenter", "Environment")
        if not all(tags.get(tag) for tag in required):
            missing_or_bad.append(f"{resource_type}:{resource_id}:required_tags")
        if resource_type == "s3":
            if tags.get("Project") == "red-queen":
                preserved += 1
            else:
                missing_or_bad.append(f"s3:{resource_id}:Project_tag_lost")
    result_counts = Counter(row["status"] for row in rows)
    latencies = [row["latency_ms"] for row in rows]
    summary = {
        "resource_events": size,
        "repetition": repetition,
        "success_count": sum(row["success"] for row in rows),
        "error_count": sum(not row["success"] for row in rows),
        "success_rate_percent": 100 * sum(row["success"] for row in rows) / size,
        "mean_latency_ms": statistics.fmean(latencies),
        "p50_latency_ms": _percentile(latencies, 0.50),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "elapsed_seconds": elapsed_ns / 1_000_000_000,
        "throughput_events_per_second": size / (elapsed_ns / 1_000_000_000),
        "tagged_resources": result_counts["tagged"],
        "preserved_s3_project_tags": preserved,
        "resource_tag_write_counts": dict(backend.calls),
        "functional_errors": missing_or_bad,
    }
    summary["tag_verification_pass"] = (
        not missing_or_bad
        and summary["tagged_resources"] == size
        and preserved == size // len(RESOURCE_TYPES)
    )
    return rows, summary


def _run_functional_checks(modules, active_backend):
    checks = {}
    backend = InMemoryBackend()
    active_backend["value"] = backend
    Harness(backend, modules)
    tagger, evaluator = modules.tagger, modules.evaluator
    original_tagger_dry_run = tagger.DRY_RUN
    original_evaluator_dry_run = evaluator.DRY_RUN

    try:
        tagger.DRY_RUN = False
        duplicate_id = "i-bench-duplicate"
        _seed_resource(backend, "ec2", duplicate_id)
        duplicate_event = _creation_event("ec2", duplicate_id)
        first = _extract_result(
            tagger.lambda_handler(duplicate_event, None), "ec2"
        )
        tag_writes_after_first = backend.calls["ec2.create_tags"]
        second = _extract_result(
            tagger.lambda_handler(duplicate_event, None), "ec2"
        )
        checks["repeated_creation_event"] = {
            "pass": (
                first["status"] == "tagged"
                and second["status"] == "already_compliant"
                and backend.calls["ec2.create_tags"] == tag_writes_after_first
            ),
            "first_status": first["status"],
            "repeat_status": second["status"],
            "tag_writes_first": tag_writes_after_first,
            "tag_writes_after_repeat": backend.calls["ec2.create_tags"],
        }

        alert_id = "i-bench-repeated-config"
        config_event = {"detail": {"configurationItem": {
            "resourceId": alert_id,
            "resourceType": "AWS::EC2::Instance",
            "tags": {},
        }}}
        first_alert = evaluator.lambda_handler(config_event, None)
        schedules_after_first = backend.calls["scheduler.create_schedule"]
        metric_writes_after_first = backend.calls["cloudwatch.put_metric_data"]
        repeated_alert = evaluator.lambda_handler(config_event, None)
        checks["repeated_config_event"] = {
            "pass": (
                first_alert.get("status") == "alerted"
                and repeated_alert.get("reason") == "already_alerted"
                and backend.calls["scheduler.create_schedule"] == schedules_after_first == 1
                and backend.calls["cloudwatch.put_metric_data"] == metric_writes_after_first == 1
            ),
            "first_status": first_alert.get("status"),
            "repeat_reason": repeated_alert.get("reason"),
            "schedule_creations": backend.calls["scheduler.create_schedule"],
            "metric_api_writes": backend.calls["cloudwatch.put_metric_data"],
            "sns_api_writes": backend.calls["sns.publish"],
        }

        dry_backend = InMemoryBackend()
        active_backend["value"] = dry_backend
        Harness(dry_backend, modules)
        tagger.DRY_RUN = True
        evaluator.DRY_RUN = True
        dry_s3_id = "rq-bench-s3-dry-run"
        _seed_resource(dry_backend, "s3", dry_s3_id)
        before_tags = dict(dry_backend.tags["s3"][dry_s3_id])
        dry_tag_result = _extract_result(
            tagger.lambda_handler(_creation_event("s3", dry_s3_id), None), "s3"
        )
        dry_config_result = evaluator.lambda_handler({"detail": {"configurationItem": {
            "resourceId": "i-bench-dry-run",
            "resourceType": "AWS::EC2::Instance",
            "tags": {},
        }}}, None)
        checks["dry_run_has_no_writes"] = {
            "pass": (
                dry_tag_result.get("status") == "dry_run"
                and dry_config_result.get("status") == "alerted"
                and dry_backend.write_count() == 0
                and dry_backend.tags["s3"][dry_s3_id] == before_tags
                and not dry_backend.records
            ),
            "auto_tagger_status": dry_tag_result.get("status"),
            "evaluator_status": dry_config_result.get("status"),
            "write_api_calls": {
                name: dry_backend.calls[name]
                for name in sorted(WRITE_METHODS)
                if dry_backend.calls[name]
            },
            "preserved_s3_tags": dry_backend.tags["s3"][dry_s3_id],
            "governance_records": sum(map(len, dry_backend.records.values())),
        }

        s3_id = "rq-bench-s3-preserve"
        preserve_backend = InMemoryBackend()
        active_backend["value"] = preserve_backend
        Harness(preserve_backend, modules)
        tagger.DRY_RUN = False
        _seed_resource(preserve_backend, "s3", s3_id)
        result = _extract_result(
            tagger.lambda_handler(_creation_event("s3", s3_id), None), "s3"
        )
        final_tags = preserve_backend.tags["s3"][s3_id]
        checks["s3_existing_tags_preserved"] = {
            "pass": (
                result.get("status") == "tagged"
                and final_tags.get("Project") == "red-queen"
                and all(final_tags.get(key) for key in
                        ("Owner", "Squad", "CostCenter", "Environment"))
            ),
            "status": result.get("status"),
            "final_tags": final_tags,
        }
    finally:
        tagger.DRY_RUN = original_tagger_dry_run
        evaluator.DRY_RUN = original_evaluator_dry_run
    return checks


def _summarize_scenarios(repetitions):
    summaries = []
    for size in SIZES:
        runs = [run for run in repetitions if run["resource_events"] == size]
        all_rows = [row for run in runs for row in run["rows"]]
        latencies = [row["latency_ms"] for row in all_rows]
        elapsed = sum(run["summary"]["elapsed_seconds"] for run in runs)
        total_events = len(all_rows)
        successes = sum(row["success"] for row in all_rows)
        summaries.append({
            "resource_events_per_repetition": size,
            "repetitions": len(runs),
            "total_events": total_events,
            "success_count": successes,
            "error_count": total_events - successes,
            "success_rate_percent": 100 * successes / total_events,
            "mean_latency_ms": statistics.fmean(latencies),
            "p50_latency_ms": _percentile(latencies, 0.50),
            "p95_latency_ms": _percentile(latencies, 0.95),
            "mean_throughput_events_per_second": total_events / elapsed,
            "per_repetition": [run["summary"] for run in runs],
        })
    return summaries


def _deny_network(*args, **kwargs):
    raise RuntimeError("Network access is disabled during the local benchmark")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "benchmark-results")
    args = parser.parse_args()
    if args.repeats < 2:
        parser.error("--repeats must be at least 2")

    args.output_dir = args.output_dir.resolve()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backend_holder = {"value": None}
    active_modules = None
    raw_repetitions = []
    all_rows = []
    functional_checks = {}
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    try:
        socket.socket.connect = _deny_network
        socket.socket.connect_ex = _deny_network
        initial_backend = InMemoryBackend()
        backend_holder["value"] = initial_backend
        active_modules, boto3, client_factory, resource_factory = _load_real_modules(
            backend_holder
        )
        with patch.object(boto3, "client", side_effect=client_factory), patch.object(
            boto3, "resource", side_effect=resource_factory
        ):
            _run_warmup(active_modules, backend_holder)
            functional_checks = _run_functional_checks(
                active_modules, backend_holder
            )
            for size in SIZES:
                for repetition in range(1, args.repeats + 1):
                    rows, summary = _run_scenario(
                        size, repetition, active_modules, backend_holder
                    )
                    all_rows.extend(rows)
                    raw_repetitions.append({
                        "resource_events": size,
                        "rows": rows,
                        "summary": summary,
                    })
    finally:
        socket.socket.connect = original_connect
        socket.socket.connect_ex = original_connect_ex

    summary = _summarize_scenarios(raw_repetitions)
    functional_checks["timed_scenario_tag_verification"] = {
        "pass": all(
            run["summary"]["tag_verification_pass"]
            for run in raw_repetitions
        ),
        "failures": [
            {
                "resource_events": run["resource_events"],
                "repetition": run["summary"]["repetition"],
                "errors": run["summary"]["functional_errors"],
                "tagged_resources": run["summary"]["tagged_resources"],
                "preserved_s3_project_tags": run["summary"][
                    "preserved_s3_project_tags"
                ],
            }
            for run in raw_repetitions
            if not run["summary"]["tag_verification_pass"]
        ],
    }
    csv_path = args.output_dir / f"red-queen-local-{run_stamp}.csv"
    json_path = args.output_dir / f"red-queen-local-{run_stamp}.json"
    columns = [
        "scenario_events", "repetition", "event_index", "resource_type",
        "resource_id", "latency_ms", "success", "status", "error",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        writer.writerows(all_rows)

    report = {
        "run_id": run_stamp,
        "command": f"python -B scripts/benchmark_local.py --repeats {args.repeats}",
        "measurement": {
            "scope": "real auto-tagger handler; one simulated CloudTrail creation event per timed call",
            "aws_api_boundary": "stateful in-memory test doubles; no AWS requests",
            "network": "socket.connect and socket.connect_ex blocked during benchmark execution",
            "resource_mix": "even round-robin across EC2, S3, RDS, and Lambda",
            "sizes": list(SIZES),
            "repetitions_per_size": args.repeats,
            "warmup": "one untimed event for each resource type",
            "latency": "time.perf_counter_ns around each handler invocation; includes handler and mock calls",
            "percentile_method": "inclusive linear interpolation (PERCENTILE.INC style)",
            "throughput": "events divided by elapsed wall-clock time for the timed handler loop",
            "measurement_excludes": "event generation, mock seeding, and post-run functional assertions",
            "limitations": "local Python plus in-memory mock overhead; not AWS service latency or production capacity",
        },
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
        },
        "scenarios": summary,
        "functional_checks": functional_checks,
        "raw_events": all_rows,
    }
    json_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"CSV: {csv_path.relative_to(ROOT) if csv_path.is_relative_to(ROOT) else csv_path}")
    print(f"JSON: {json_path.relative_to(ROOT) if json_path.is_relative_to(ROOT) else json_path}")
    print(json.dumps({"scenarios": summary, "functional_checks": functional_checks}, indent=2))
    if any(not check["pass"] for check in functional_checks.values()):
        return 1
    if any(not row["success"] for row in all_rows):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
