# sensible/ — secrets for the local Grafana stack

| File | Purpose | In git? |
|------|---------|---------|
| `.env.example` | Template, no real values | yes |
| `.env` | Real Grafana password + AWS read-only keys | **no** (gitignored) |

```bash
cp sensible/.env.example sensible/.env
# fill in GRAFANA_ADMIN_PASSWORD, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
docker compose up -d        # run from the repository root
```

The AWS key only needs `cloudwatch:GetMetricData`, `cloudwatch:ListMetrics` and
`cloudwatch:GetMetricStatistics` — create a dedicated read-only IAM user for it.

Terraform state and `terraform.tfvars` live in `infra/` and are also gitignored.
For anything beyond a personal sandbox, use a remote backend (S3 + DynamoDB lock).
