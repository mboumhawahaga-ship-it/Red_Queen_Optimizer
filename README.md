# AWS Cloud Cost Allocation & Tagging Governance Framework

A serverless FinOps automated governance solution designed to eliminate unallocated cloud spend, enable strict cost chargeback, and enforce operational accountability across multi-team AWS environments—without disrupting engineering velocity.

---

## 🎯 Business Value & FinOps Impact

In multi-team organizations, untagged cloud infrastructure directly translates to financial waste and budget blindness. When engineering provisions resources without accounting metadata, Finance cannot allocate costs, causing untracked spend and orphaned resources.

This framework bridges the gap between Cloud Engineering and Corporate Finance by automated enforcement of a **4-tag policy** (`Owner`, `Squad`, `CostCenter`, `Environment`).

### Key Business Outcomes

- **100% Cost Visibility:** Transforms "Invisible Spend" into accountable, audit-ready data for Cost Explorer and ERP systems.
- **Automated Chargeback & Showback:** Enables Finance to accurately split the AWS bill by Business Unit, Squad, and Cost Center.
- **Waste Elimination:** Automatically flags orphaned resources to their respective engineering squads before month-end financial closure.
- **Zero Engineering Friction:** Enforces compliance through non-destructive alerting and automated metadata discovery, replacing aggressive resource-deletion scripts that break production.

---

## 📊 Business Key Performance Indicators (KPIs)

This framework is built to drive corporate FinOps success metrics. It provides leadership with the exact data points required to prove governance ROI:

| Executive KPI | Measurement Metric | Business Target |
|---|---|---|
| **Allocated Spend %** | (Tagged CostCenter Spend / Total AWS Spend) via Cost Explorer | > 90% of total cloud invoice allocated |
| **Unallocated Waste** | Month-over-Month (MoM) trend of "No Tag" / untagged dollar amount | Decreasing MoM toward zero |
| **Operational Ownership** | Coverage of the `Squad` and `Owner` tags across all running services | 100% accountability matrix |
| **Data Integrity Rate** | Percentage of fake/placeholder values rejected by the framework | 0% placeholder tolerance |
| **Mean Time to Remediate** | Time from violation detection to compliant tagging (SLA Tracker) | Critical: < 36h \| Non-Critical: < 7 days |

---

## 🛠️ Strategic Architecture & Governance Flow

The architecture is entirely event-driven, cost-optimized (Serverless Pay-per-Use), and acts as the financial control plane for AWS resources.

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. GOVERNANCE PLANE (This Framework)                            │
│    Auto-Tagging on Creation · Continuous Compliance Monitoring   │
│    Placeholder Value Rejection · Automated SLA Alerting         │
└───────────────────────────────┬─────────────────────────────────┘
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│ 2. FINANCIAL INGESTION (AWS Billing)                            │
│    Activation of User-Defined Cost Allocation Tags              │
└───────────────────────────────┬─────────────────────────────────┘
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│ 3. EXECUTIVE PROOF PLANE (Cost Explorer / Dashboards)           │
│    Cost Allocation Matrix · Financial Reporting for Leadership  │
└─────────────────────────────────────────────────────────────────┘
```

### Technical Blueprint

- **Real-Time Contextual Auto-Tagger:** Monitors CloudTrail via EventBridge. On resource creation, a Lambda function extracts IAM context and dynamically injects operational metadata (e.g., `Owner` from IAM identity) without overwriting manual inputs.

- **Continuous Compliance Engine:** AWS Config evaluates resource state continuously against business rules. It strictly rejects placeholder compliance bypassing (e.g., `CostCenter=unassigned` or `unknown`).

- **Tiered Business SLAs:**
  - **CRITICAL (36h SLA):** Production environments, RDS Databases, High-Cost EC2 Instances. Immediate SNS/Slack alerting to Squad Leads.
  - **NON_CRITICAL (7-day SLA):** Non-production environments, ephemeral workloads.

- **Feedback & Exception API:** An API Gateway backed by DynamoDB allows engineering leads to legally snooze alerts (24h) for maintenance windows or mark issues as `handled`, providing operational flexibility without breaking compliance.

---

## 📋 FinOps Playbook: Generating Executive Proof

To prove the financial impact to Stakeholders and CFOs, follow this reporting protocol:

### 1. Financial Data Ingestion

1. Navigate to **AWS Billing** ➔ **Cost allocation tags**.
2. Activate: `CostCenter`, `Squad`, `Environment`, and `Owner`.
3. Allow **24–48 hours** for data propagation into financial systems.

### 2. Executive Cost Allocation Reporting

Execute the following query via AWS CLI or mirror the filters in Cost Explorer to generate the monthly Business Unit spend breakdown:

```bash
aws ce get-cost-and-usage \
  --time-period Start=2026-05-01,End=2026-06-01 \
  --granularity MONTHLY \
  --metrics UnblendedCost \
  --group-by Type=TAG,Key=CostCenter
```

> 💡 **The FinOps Proof:** Run this report the month prior to deployment vs. one month post-deployment. The business success is proven by the sharp percentage increase of allocated spend and the reduction of the "Untagged / No Tag" line item.

---

## 🚀 Deployment & Operational Readiness

### Repository Structure

```
aws-tagging-governance/
├── lambdas/                   # Core business & compliance logic (Python 3.12)
│   ├── auto-tagger/           # Real-time metadata injection
│   ├── compliance-evaluator/  # SLA tracking & placeholder validation
│   └── feedback-api/          # Engineering lifecycle management
├── infra/                     # Infrastructure as Code (Terraform >= 1.5)
│   ├── config.tf              # AWS Config governance rules
│   └── apigw.tf, eventbridge.tf, dlq.tf
└── tests/                     # Enterprise stability (37 unit/integration tests)
```

### Enterprise Rollout Strategy

1. **Pilot Phase:** Deploy to a single non-production workload account with `dry_run = true` to observe compliance baselines via the CloudWatch Executive Dashboard (`tagging-gov-<env>-overview`).
2. **Multi-Account Scaling:** Scale horizontally across the AWS Organization by isolating state files per account (`platform-dev`, `platform-prod`, `data-prod`) utilizing standard `terraform.tfvars` configurations.
3. **Enforcement Phase:** Transition `dry_run = false` to activate active alerting and SLA tracking.

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
# Configure environment-specific notification channels & variables
terraform init
terraform plan
terraform apply
```

---

*Developed with a FinOps-first mindset. Ensuring every dollar spent in the cloud is a dollar accounted for.*
