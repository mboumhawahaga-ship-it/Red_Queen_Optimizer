# Red Queen local benchmark

## What this benchmark measures

This benchmark runs the project's real Python auto-tagger handler against
simulated CloudTrail resource-creation events. The workload is split evenly
across EC2, S3, RDS, and Lambda resources. The handler, tag rules, result
handling, and shared DynamoDB persistence helper are real project code.

AWS API boundaries are replaced by small, stateful in-memory doubles. Network
socket connections are blocked for the benchmark run. The runner sets
fictitious placeholder credentials only to prevent SDK credential discovery;
no real AWS credentials, account, deployment, or live AWS service is used.

The timed workload measures one handler invocation per unique resource event.
Compliance-alert retries and dry-run behavior are checked separately as
functional scenarios; they are not included in the latency or throughput
figures.

## Results

Run on 15 November 2026 using Python 3.12.2 on Windows 11. Each workload size
was run five times, after one untimed warm-up event per resource type.

| Events per repetition | Repetitions | Total events | Successful | Errors | Success rate | Mean latency | P50 | P95 | Combined throughput |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 200 | 5 | 1,000 | 1,000 | 0 | 100% | 0.132645 ms | 0.118250 ms | 0.216600 ms | 7,443.08 events/s |
| 1,000 | 5 | 5,000 | 5,000 | 0 | 100% | 0.126171 ms | 0.113300 ms | 0.184310 ms | 7,822.06 events/s |

Across all 6,000 timed calls, no handler exceptions or unexpected results were
observed. The benchmark did not inject API failures; zero errors here does not
measure resilience to live AWS errors.

Throughput is total events divided by the combined timed loop duration across
the five repetitions. Latency is measured around each handler call using
`time.perf_counter_ns`; it includes Python handler work and in-memory mock
operations. P50/P95 use inclusive linear interpolation. Event generation,
mock-state setup, warm-up, and post-run assertions are outside the timed
interval.

Per-repetition throughput varied from **5,783.77 to 8,822.12 events/s** for
the 200-event case and from **7,114.82 to 8,467.67 events/s** for the
1,000-event case. Per-run P95 ranged from **0.144335 to 0.284755 ms** and
**0.170640 to 0.204600 ms**, respectively. These short local runs are sensitive
to host load and should not be treated as capacity guarantees.

## Functional checks

- **Resources processed:** all timed events returned `tagged`; required
  `Owner`, `Squad`, `CostCenter`, and `Environment` tags were present.
- **Existing S3 tags:** the pre-existing `Project=red-queen` tag remained
  alongside the added governance tags for every S3 resource.
- **Repeated creation event:** the first EC2 event tagged the resource; the
  repeat returned `already_compliant` and did not add another resource-tag
  write.
- **Repeated Config finding:** the first finding created one alert, one
  schedule, and one metric call; the repeat returned `already_alerted` without
  another schedule or metric call. Email and Slack destinations were left
  unconfigured, so delivery was not tested.
- **Dry-run:** both auto-tagger and compliance evaluator were invoked in
  dry-run mode. No tag, persistence, notification, schedule, or metric write
  occurred; existing S3 tags remained unchanged.

All five functional checks passed in the saved run.

## Reproduce

From the repository root, with the project development dependencies available:

```powershell
python -B scripts/benchmark_local.py --repeats 5 --output-dir benchmark-results
```

The script runs the 200-event workload and then the 1,000-event workload,
creates a timestamped CSV and JSON report, and exits non-zero if an event or
functional check fails. The CSV contains one row per timed event, including its
latency, outcome, and any error. The JSON includes the raw event rows, per-run
and combined summaries, environment details, and functional-check evidence.

### Raw results for this run

- [CSV — 6,000 event measurements](../benchmark-results/red-queen-local-20261115T113038Z.csv)
- [JSON — summaries, raw events, and functional checks](../benchmark-results/red-queen-local-20261115T113038Z.json)
- [Benchmark runner](../scripts/benchmark_local.py)

## Limits

These are measurements of local Python code plus in-memory test doubles—not
AWS Lambda duration, EventBridge delivery, network latency, DynamoDB capacity,
or service throughput. They do not model Lambda cold starts, distributed
concurrency, retries under load, realistic AWS throttling, or customer data
sizes. The workload is sequential and does not test simultaneous S3 tag
updates. The functional checks demonstrate selected code paths only; they are
not an AWS end-to-end integration test or a production readiness assessment.
