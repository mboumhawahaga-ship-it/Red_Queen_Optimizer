# AWS Tagging Governance

serverless AWS tagging enforcement for **FinOps chargeback** and **operational ownership** — no resource deletion.

[![CI](https://github.com/mboumhawahaga-ship-it/Red_Queen_Optimizer/actions/workflows/ci.yml/badge.svg)](https://github.com/mboumhawahaga-ship-it/Red_Queen_Optimizer/actions/workflows/ci.yml)

---

## Why this exists (FinOps problem)

In multi-team AWS accounts, spend without standard tags creates two problems:

| Problem | Business impact |
|---------|-----------------|
| **Unallocated spend** | Finance cannot charge back by squad, cost center, or environment |
| **Orphan resources** | No accountable owner → waste continues unnoticed |

This project automates a **four-tag policy** so Cost Explorer can split the bill. It does **not** replace AWS Billing — it makes tags **real** (present, meaningful, maintained).

**Required tags:** `Owner`, `Squad`, `CostCenter`, `Environment`

---

## How to prove cost allocation (FinOps playbook)

Tagging governance and **provable cost** are two linked steps. This repo handles step 1; you prove step 2 in Billing.

### The chain

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. THIS PROJECT          Tags on resources (governance)        │
│    Auto-tag at create · Config compliance · reject placeholders │
└───────────────────────────────┬─────────────────────────────────┘
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. AWS BILLING (manual once)  Cost allocation tags activated   │
│    Billing → Cost allocation tags → activate CostCenter, Squad… │
└───────────────────────────────┬─────────────────────────────────┘
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. COST EXPLORER (proof)   Reports & KPIs for leadership       │
│    Group by CostCenter / Squad · track % allocated spend        │
└─────────────────────────────────────────────────────────────────┘
```

### Step 1 — What this project already gives you

| Capability | FinOps value |
|------------|--------------|
| Auto-tagger on create | New resources are not born "invisible" to finance |
| AWS Config `REQUIRED_TAGS` | Ongoing check — tags removed later are caught |
| Placeholder rejection (`unknown`, `unassigned`) | Tags exist for **allocation**, not checkbox compliance |
| Alerts + SLA | Owners fix tags before month-end close |
| CloudWatch metrics | Trend of violations (ops), not euros (Billing) |

**Important:** CloudWatch shows *compliance*; **Cost Explorer shows money**.

### Step 2 — Activate cost allocation tags (one-time per account)

1. Open **AWS Billing** → **Cost allocation tags**.
2. Activate user-defined tags: `CostCenter`, `Squad`, `Environment`, `Owner`.
3. Wait **24–48 hours** for data to appear in Cost Explorer.

Without this step, tags on resources **do not** show in cost reports.

### Step 3 — KPIs that prove value (show leadership)

Track monthly (spreadsheet or dashboard):

| KPI | How to measure | Target (example) |
|-----|----------------|------------------|
| **Allocated spend %** | Cost Explorer → filter tagged `CostCenter` ≠ empty / total spend | > 90% |
| **Unallocated spend $** | Group by `CostCenter` → row "No tag" / untagged | Decreasing MoM |
| **Spend by Squad** | Group by tag `Squad` | Every squad visible |
| **Placeholder rate** | CloudWatch / DynamoDB: resources with violations | → 0 |
| **Time to remediate** | `first_seen` → tags fixed (DynamoDB / logs) | < SLA (36h / 7d) |

### Step 4 — Cost Explorer report (copy-paste proof)

**Monthly spend by cost center:**

1. Cost Explorer → **Cost and usage reports**.
2. Granularity: **Monthly**.
3. Group by: **Tag** → `CostCenter`.
4. Filter: Service (optional) EC2, RDS, S3, Lambda.

**Screenshot or CSV export** = proof for FinOps / management.

**CLI example** (after tags are activated in Billing):

```bash
aws ce get-cost-and-usage \
  --time-period Start=2026-05-01,End=2026-06-01 \
  --granularity MONTHLY \
  --metrics UnblendedCost \
  --group-by Type=TAG,Key=CostCenter
```

**Before / after pilot:** Run the same report the month before deploy vs one month after — show `% allocated` increase.

### What counts as "proof" in a review

| Evidence | Audience |
|----------|----------|
| Cost Explorer grouped by `CostCenter` / `Squad` | Finance, management |
| KPI trend: allocated % ↑ | FinOps lead |
| CloudWatch dashboard: violations ↓ | Engineering / platform |
| Sample SNS alert + fixed resource | Squad leads |

This project is the **control plane**; Cost Explorer is the **proof plane**.

---

## Architecture

```
CloudTrail (resource created)
        │
        ▼
EventBridge ──► Auto-Tagger Lambda
                  Applies defaults on missing keys (never overwrites)
                  Does NOT set fake FinOps values (no CostCenter=unassigned)
                  DynamoDB status=auto_tagged · metric AutoTagApplied

AWS Config (NON_COMPLIANT)
        │
        ▼
EventBridge ──► Compliance Evaluator Lambda
                  Skips handled / active snooze
                  Rejects placeholder tag values
                  CRITICAL (36h SLA) vs NON_CRITICAL (7d)
                  SNS/Slack with alert cooldown (6h / 24h)
                  DynamoDB · metric TagComplianceViolation

API Gateway (x-api-key) ──► Feedback API
                  POST /feedback  handled | snoozed
                  GET  /status
```

**Supported services:** EC2, RDS, S3, Lambda.

---

## Design principles

| Principle | Implementation |
|-----------|----------------|
| No deletion | Tag + alert only — never terminate resources |
| Event-driven | EventBridge → Lambda (no Step Functions) |
| Human in the loop | Feedback API: `handled` or `snoozed` (24h) |
| Safe rollout | `dry_run = true` by default on auto-tagger |
| Fail visible | SQS DLQ + alarm if events fail |
| Meaningful tags | Placeholders rejected; auto-tagger skips fake Owner/CostCenter |

---

## Required tags & SLAs

| Tag | Example | FinOps use |
|-----|---------|------------|
| `Owner` | `jane.doe@company.com` | Accountability |
| `Squad` | `DataEngineering` | Team showback |
| `CostCenter` | `CC-123` | GL / chargeback |
| `Environment` | `dev` / `prod` | Env split, priority |

| Criticality | When | Remediation SLA |
|-------------|------|-----------------|
| `CRITICAL` | All RDS; EC2 `Environment=prod`; `CriticalWorkload=true` | 36 hours |
| `NON_CRITICAL` | Everything else | 7 days |

**Alert cooldown:** max one SNS/Slack per resource every **6h** (CRITICAL) or **24h** (NON_CRITICAL), unless violation tags change.

---

## Auto-tagger defaults (missing keys only)

```
Squad       = "unknown" (configurable)
Environment = var.environment (e.g. dev)
Status      = needs-review
ManagedBy   = AutoTagger
Owner       = IAM email only if detected
CostCenter  = not set by auto-tagger (team must provide a real value)
```

---

## Feedback API

```bash
export API_URL="$(terraform output -raw feedback_api_url)"
export API_KEY="$(terraform output -raw feedback_api_key)"
```

```bash
# Mark resolved (excluded from future evaluations)
curl -X POST "${API_URL}/feedback" \
  -H "Content-Type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -d '{"resource_id": "i-0abc123", "action": "handled"}'

# Snooze 24h
curl -X POST "${API_URL}/feedback" \
  -H "Content-Type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -d '{"resource_id": "i-0abc123", "action": "snoozed"}'

# Status
curl -H "x-api-key: ${API_KEY}" "${API_URL}/status?resource_id=i-0abc123"
```

Missing or invalid API key → **403** (Lambda not invoked).

---

## Project structure

```
aws-tagging-governance/
├── lambdas/
│   ├── shared/
│   ├── compliance-evaluator/
│   ├── auto-tagger/
│   └── feedback-api/
├── infra/
│   ├── config.tf          # AWS Config recorder + REQUIRED_TAGS rule
│   ├── dlq.tf             # SQS dead-letter queue
│   ├── eventbridge.tf
│   ├── apigw.tf
│   └── ...
├── tests/
├── docs/
│   ├── architecture.md
│   └── JOURNAL_DE_BORD.md   # design decisions (French)
└── requirements-dev.txt
```

---

## Prerequisites

| Tool | Version |
|------|---------|
| AWS CLI | configured |
| Terraform | >= 1.5 |
| Python | 3.12 |

| AWS | Notes |
|-----|-------|
| **AWS Config** | Created by Terraform (`infra/config.tf`). Set `enable_aws_config = false` if org already manages Config. |
| **CloudTrail** | Required for auto-tagger (resource creation events). |
| **Cost allocation tags** | Manual in Billing console (see FinOps playbook above). |

---

## Multi-account deployment (not automatic)

**This repository is single-account per `terraform apply`.** AWS Config does not make it multi-account — it only evaluates resources in the account where you deploy.

To cover an organization, use the **same stack once per account**:

```
AWS Organizations
        │
        ├── Account: platform-dev     → terraform apply (environment = dev)
        ├── Account: platform-prod    → terraform apply (environment = prod)
        └── Account: data-prod        → terraform apply (environment = prod)
```

| Topic | Per-account behavior |
|-------|----------------------|
| Lambdas, EventBridge, DynamoDB, SNS | Isolated in each account |
| AWS Config (`config.tf`) | Recorder + `REQUIRED_TAGS` rule in each account |
| Cost allocation proof | Activate tags in **each** account (or use management/payer account Cost Explorer for org-wide spend) |
| Tag keys | Keep identical (`Owner`, `Squad`, `CostCenter`, `Environment`) everywhere |

**Recommended rollout**

1. Pilot one non-production account → validate tags, alerts, Cost Explorer sample.
2. Copy `terraform.tfvars` pattern per account (change `environment`, `notification_email`, optional `feedback_api_key`).
3. Run `terraform apply` with credentials/role for **that** account (separate state file or backend key per account).
4. Optional later: CI pipeline or CloudFormation StackSets to repeat the same module — no code fork required.

```hcl
# infra/terraform.tfvars — example: prod workload account
aws_region         = "eu-west-1"
environment        = "prod"
notification_email = "cloud-governance@company.com"
dry_run            = false
enable_aws_config  = true   # false only if org already runs Config in this account
```

**Not included in v2 (org-wide extras):** AWS Config Aggregator, Organizations tag policies, SCPs — typically owned by the cloud foundation team alongside this project.

---

## Deploy

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
# Set notification_email (required)

terraform init
terraform plan
terraform apply
```

After apply:

1. Confirm SNS email subscription.
2. Save outputs: `feedback_api_key`, `feedback_api_url`, `cloudwatch_dashboard_url`.
3. Activate cost allocation tags in Billing (FinOps).
4. Start with `dry_run = true`, then set `dry_run = false` when ready.

---

## Development

```bash
pip install -r requirements-dev.txt
pytest tests/   # 37 tests
```

CI: Python tests + `terraform validate` on every push ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)).

---

## Terraform outputs

| Output | Use |
|--------|-----|
| `feedback_api_url` | API base URL |
| `feedback_api_key` | `x-api-key` header (sensitive) |
| `cloudwatch_dashboard_url` | Compliance ops dashboard |
| `dynamodb_table_name` | Governance state |
| `lambda_dlq_url` | Failed events queue |
| `config_rule_name` | AWS Config rule name |

---

## Observability

- **Dashboard:** `tagging-gov-<env>-overview`
- **Metrics:** `TaggingGovernance` — `TagComplianceViolation`, `AutoTagApplied`
- **Alarms:** CRITICAL violations, Lambda errors, DLQ not empty
- **Logs:** KMS-encrypted, retention via `log_retention_days`

Logs Insights example:

```
fields @timestamp, resource_id, criticality, missing_tags
| filter status = "non_compliant"
| sort @timestamp desc
| limit 50
```

---

## Owner journey

1. Resource created → auto-tagger applies operational defaults.
2. Missing `Owner` / `CostCenter` → Config NON_COMPLIANT → SNS alert.
3. Owner sets real tags (chargeback-ready) or uses feedback API.
4. FinOps runs Cost Explorer by `CostCenter` / `Squad` to prove allocation.

---

## Further reading

- [docs/architecture.md](docs/architecture.md) — technical flows & DynamoDB schema
- [docs/JOURNAL_DE_BORD.md](docs/JOURNAL_DE_BORD.md) — design log (French)
