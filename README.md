# Red Queen — AWS Tag Governance

Serverless platform that enforces mandatory tags on AWS resources so that cloud costs can be attributed to a team, an owner and a cost center. Built as a self-taught FinOps / infrastructure project: Terraform, Python 3.12 Lambdas, unit tests, CI, Grafana.

Every number in this README can be checked in the repository.

---

## The problem

Cost allocation in AWS depends on tags. Without `Owner`, `Squad`, `CostCenter` and `Environment` on every resource, a monthly bill is just a total: nobody knows which team spent what, orphaned resources keep running, and finance cannot charge back.

In practice tags go missing all the time: a quick test instance, a bucket created from the console, a Lambda deployed by hand. Asking people to "remember to tag" does not scale.

## The goal

Make untagged resources impossible to ignore, without blocking engineers:

1. Tag new resources automatically with sensible defaults, at creation time.
2. Detect resources that are still missing required tags.
3. Notify the owner and start an SLA clock.
4. If the SLA expires, quarantine the resource (tag it `Status=needs-review`) — never delete it.
5. Give owners a way to respond (fix, dispute, or mark as intentionally exempt).
6. Measure everything on a dashboard.

## What is actually implemented

| Capability | Where | Status |
|---|---|---|
| Auto-tagging on creation (EC2, S3, RDS, Lambda) | `lambdas/auto-tagger` | done |
| Compliance evaluation on AWS Config findings | `lambdas/compliance-evaluator` | done |
| Criticality classification (CRITICAL / NON_CRITICAL) | `lambdas/shared/utils.py` | done |
| SLA enforcement: 36 h critical, 7 days non-critical | `lambdas/shared/constants.py` | done |
| One-shot SLA timer via EventBridge Scheduler | `compliance-evaluator` | done |
| Quarantine by tagging (no stop, no delete) | `compliance-evaluator` | done |
| Email (SNS) + Slack notifications | `lambdas/shared/utils.py` | done |
| Owner feedback API (approve / tag / reject) | `lambdas/feedback-api` + API Gateway | done |
| Governance event history with 90-day TTL | DynamoDB | done |
| CloudWatch metrics per outcome + Grafana dashboard | `compliance-evaluator`, `grafana/` | done |
| Dry-run mode (default) | all Lambdas | done |
| Multi-account (AWS Organizations) | — | not started |
| Budget alerts | — | not started |

## Architecture

```
 CloudTrail "Create*" event ──► EventBridge ──► auto-tagger ──► applies default tags
                                                                 └─► DynamoDB (event=auto_tag)

 AWS Config NON_COMPLIANT ───► EventBridge ──► compliance-evaluator
                                                 ├─► SNS email + Slack alert
                                                 ├─► DynamoDB (event=alerted, sla_deadline)
                                                 ├─► CloudWatch metric (Outcome=alerted)
                                                 └─► EventBridge Scheduler one-shot at SLA deadline
                                                                │
                                          SLA deadline ─────────┘
                                                 │
                                       re-check live tags
                                       ├─ compliant     → DynamoDB resolved    + metric
                                       └─ still missing → quarantine tags + DynamoDB quarantined + metric

 Owner ──► API Gateway (IAM auth) ──► feedback-api ──► DynamoDB (handled / resolved / alerted)

 Grafana (local, Docker) ◄── CloudWatch RedQueen/Governance metrics
```

Two separate EventBridge rules route CloudTrail events to `auto-tagger` only and Config events to `compliance-evaluator` only. Merging them would let the evaluator fire before the tagger has finished.

## Governance model

**Required tags:** `Owner`, `Squad`, `CostCenter`, `Environment`

**Criticality** (evaluated in this order):

1. `CriticalWorkload=true` tag → CRITICAL
2. RDS instance → CRITICAL
3. EC2 with `Environment=prod` → CRITICAL
4. Anything else → NON_CRITICAL

| Criticality | SLA before quarantine |
|---|---|
| CRITICAL | 36 hours |
| NON_CRITICAL | 168 hours (7 days) |

**Quarantine** adds three tags (`Status=needs-review`, `QuarantinedAt`, `QuarantineReason`). The platform never stops or deletes a resource; that decision stays with a human.

**Feedback actions** (`POST /feedback`):

| Action | Resulting status | Meaning |
|---|---|---|
| `tag` | resolved | owner added the tags |
| `approve` | handled | resource is intentionally exempt, excluded from future evaluation |
| `reject` | alerted | owner disputes the finding; normal SLA flow resumes |

## Metrics and dashboard

`compliance-evaluator` publishes one `GovernanceEvents` datapoint per run to the `RedQueen/Governance` CloudWatch namespace, with dimensions `Outcome` (compliant / alerted / resolved / quarantined), `ResourceType` and `Criticality`. Metrics are published in dry-run mode too, so the dashboard can be validated before enforcement is switched on.

The Grafana dashboard (`grafana/dashboards/red-queen-governance.json`) shows those counters over time, the split by outcome, alerts by resource type, quarantines by criticality, and the native `AWS/Billing` estimated charges.

`scripts/publish_demo_metrics.py` publishes 12 sample events through the same helper the Lambda uses, so the dashboard can be seen before any real event arrives.

## Security

- Customer-managed KMS key with rotation enabled; used for DynamoDB, SNS and all four CloudWatch log groups (log groups are pre-created by Terraform so Lambda never creates unencrypted ones).
- One IAM role per Lambda (4 roles, 9 inline policies). `PutMetricData` is restricted to the project namespace; `PassRole` to the scheduler is conditioned on `iam:PassedToService`.
- Feedback API routes require AWS IAM (SigV4) authorization. Without it anyone could exempt a resource with `action=approve`.
- Resource IDs are validated against an allow-list regex before any DynamoDB or log write; the Slack webhook URL must be `https://`.
- `dry_run = true` by default: Lambdas log intent but write nothing to AWS APIs.
- DynamoDB point-in-time recovery and 90-day TTL on governance records.

## Repository layout

```
infra/          Terraform (12 files, 38 resources): Lambdas, IAM, EventBridge, API Gateway, DynamoDB, KMS, SNS, logs
lambdas/        3 Lambdas + shared module (~1,550 lines of Python)
  auto-tagger/
  compliance-evaluator/
  feedback-api/
  shared/       constants, sanitisation, classification, DynamoDB, notifications, metrics
grafana/        provisioned CloudWatch datasource + dashboard
scripts/        demo metrics publisher, tag validation script
docs/           engineering journal (errors met, fixes, lessons) — covers the earlier v1 as well
```

## Quality

- 89 unit tests (pytest + moto): 41 on the compliance evaluator, 31 on the feedback API, 9 on the shared module, plus parametrised cases.
- CI on every push: flake8, pytest on Python 3.12 (same as the Lambda runtime), `terraform fmt -check`, `terraform validate`, dashboard JSON validation.
- Single source of truth for names (`infra/locals.tf`) and for governance rules (`lambdas/shared/constants.py`).

## Getting started

Prerequisites on the target account and region (not created by this Terraform):

- a CloudTrail trail delivering management events (needed for `auto-tagger` to receive create events),
- an AWS Config recorder with a `required-tags` managed rule for `Owner`, `Squad`, `CostCenter`, `Environment`.

```bash
# 1. Configure
cp infra/terraform.tfvars.example infra/terraform.tfvars   # set sns_email, optionally slack_webhook_url

# 2. Deploy — dry-run is on by default
cd infra
terraform init
terraform apply
# confirm the SNS subscription email

# 3. Run the tests locally
cd ..
pip install -r requirements-dev.txt
pytest lambdas -q

# 4. Dashboard
cp sensible/.env.example sensible/.env                     # Grafana password + read-only AWS keys
docker compose up -d                                       # http://localhost:3000
python scripts/publish_demo_metrics.py                     # optional sample data
```

When alerts and the dashboard look right, set `dry_run = false` and re-apply.

## What I learned

The first version (see `docs/JOURNAL_DE_BORD.md`) deleted non-compliant resources after a grace period and orchestrated everything with Step Functions. It worked, but deletion is the wrong default for a governance tool and the orchestration was more code than the problem needed. The rewrite moved to tag-based quarantine, one-shot EventBridge schedules, and a shared module so the three Lambdas persist and notify the same way.

## Roadmap

- Terraform-managed CloudTrail and Config rule so the stack is self-contained.
- AWS Organizations / multi-account evaluation.
- Budget alerts tied to `CostCenter`.
- Remote Terraform backend (S3 + DynamoDB lock).

## License

MIT
