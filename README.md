# AWS Cloud Cost Allocation & Tagging Governance Framework

A serverless FinOps governance system designed to improve cloud cost visibility, enforce resource ownership, and enable reliable chargeback across multi-team AWS environments—without impacting delivery velocity.

---

## 🎯 Business Context

In multi-team AWS environments, lack of consistent tagging leads to:

- Unallocated cloud spend that cannot be attributed to teams
- Orphaned resources with no clear ownership
- Limited visibility for Finance and Engineering leadership

This creates a gap between cloud usage and financial accountability, making FinOps practices difficult to implement at scale.

---

## 💡 Business Objectives

This framework addresses three core FinOps goals:

- **Cost Visibility:** ensure all cloud spend can be mapped to business units
- **Cost Allocation (Chargeback/Showback):** enable accurate financial reporting per team
- **Operational Accountability:** enforce ownership of cloud resources at creation time

---

## 📊 Business Outcomes

### 1. Improved Cost Attribution

Cloud spend becomes structured by:

- Squad
- Cost Center
- Environment
- Resource Owner

→ Enables Finance to build reliable cost allocation reports in AWS Cost Explorer

### 2. Reduced Unallocated Spend

Untagged or mis-tagged resources are detected early and flagged to responsible teams before month-end reporting.

### 3. Stronger Engineering Accountability

Each resource is linked to a clear owning team, reducing:

- orphaned infrastructure
- unused resources
- unclear ownership disputes

### 4. Non-Disruptive Governance

Compliance is enforced through:

- automated tagging
- alerts
- SLA-based remediation

No destructive actions are taken on production resources.

---

## 📈 FinOps KPIs

| KPI | Description | Target |
|-----|-------------|--------|
| **Allocated Spend %** | % of AWS costs mapped to valid tags | > 90% |
| **Unallocated Spend** | Cost not assigned to any CostCenter | decreasing trend |
| **Tag Coverage** | Resources with Owner/Squad/CostCenter | 100% |
| **Placeholder Rate** | Invalid or fake values detected | ~0% |
| **Time to Remediate** | Violation → resolution time | < 36h critical / < 7 days non-critical |

> **Estimated impact:** Enables >90% cost allocation coverage through enforced tagging strategy. Reduces unallocated ("No Tag") spend typically found in 30–60% of AWS environments via continuous compliance enforcement.

---

## 🏗️ Architecture Overview

The system is fully event-driven and serverless, acting as a governance layer on top of AWS resource provisioning.

```
CloudTrail + EventBridge
        ↓
Auto-Tagging Lambda
  Applies missing operational metadata (when possible)
        ↓
AWS Config Rules
  Continuously evaluates tagging compliance
        ↓
SNS / API Layer
  Notifies and engages responsible teams
        ↓
AWS Cost Explorer
  Provides financial reporting and validation layer
```

---

## ⚙️ Governance Model

### Tag Policy (Required)

| Tag | Purpose |
|-----|---------|
| `Owner` | accountability |
| `Squad` | engineering team ownership |
| `CostCenter` | financial allocation |
| `Environment` | workload classification |

### SLA-Based Enforcement

| Severity | Scope | SLA |
|----------|-------|-----|
| **CRITICAL** | Production workloads / high-cost resources | 36h |
| **NON-CRITICAL** | Non-production / ephemeral workloads | 7 days |

---

## 🧾 FinOps Reporting Flow

1. Activate cost allocation tags in AWS Billing
2. Wait for data propagation (24–48h)
3. Use Cost Explorer grouped by `CostCenter`
4. Compare pre/post deployment allocation rates

Example query:

```bash
aws ce get-cost-and-usage \
  --time-period Start=2026-05-01,End=2026-06-01 \
  --granularity MONTHLY \
  --metrics UnblendedCost \
  --group-by Type=TAG,Key=CostCenter
```

---

## 🚀 Deployment Strategy

1. Pilot in a single AWS account (non-production)
2. Validate tagging compliance and alert flow
3. Scale per account using identical Terraform module
4. Enable enforcement after validation phase (`dry_run = false`)

```bash
cd infra
cp terraform.tfvars.example terraform.tfvars
terraform init
terraform plan
terraform apply
```

---

## 💼 Why this project matters

This framework demonstrates:

- FinOps engineering mindset (cost + governance + automation)
- AWS event-driven architecture design
- Multi-team cloud governance at scale
- Production-ready observability & SLA enforcement
- Strong alignment between Engineering and Finance
