# Single source of truth for all computed names and paths.
# Every resource name in this stack is derived from these locals.

locals {
  # ── Naming ──────────────────────────────────────────────────────────────────
  name_prefix = "${var.project}-${var.environment}"

  # ── Tags applied to every resource via provider default_tags ────────────────
  common_tags = {
    Project     = var.project
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  # ── DynamoDB ─────────────────────────────────────────────────────────────────
  governance_table_name = "${local.name_prefix}-governance-events"

  # ── Lambda function names (also used for CloudWatch log group names) ─────────
  auto_tagger_name          = "${local.name_prefix}-auto-tagger"
  compliance_evaluator_name = "${local.name_prefix}-compliance-evaluator"
  feedback_api_name         = "${local.name_prefix}-feedback-api"

  # ── Lambda runtime ────────────────────────────────────────────────────────────
  lambda_runtime = "python3.12"

  # ── Source paths (relative to infra/) ────────────────────────────────────────
  lambdas_root = "${path.root}/../lambdas"

  # ── Pre-computed compliance-evaluator ARN (avoids circular reference) ─────────
  # Lambda ARN is deterministic: we know region, account, and function name at plan time.
  compliance_evaluator_arn = "arn:aws:lambda:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:function:${local.compliance_evaluator_name}"
}
