# Each Lambda zip bundles the full lambdas/ package tree so that relative imports
# (from ..shared.xxx) resolve correctly inside the Lambda runtime.
# Hyphenated directory names are mapped to underscored module names in the zip
# (auto-tagger → auto_tagger, etc.) since Python identifiers cannot contain hyphens.

# ── auto-tagger ───────────────────────────────────────────────────────────────

data "archive_file" "auto_tagger" {
  type        = "zip"
  output_path = "${path.module}/.builds/auto_tagger.zip"

  source {
    content  = file("${local.lambdas_root}/__init__.py")
    filename = "lambdas/__init__.py"
  }
  source {
    content  = file("${local.lambdas_root}/auto-tagger/__init__.py")
    filename = "lambdas/auto_tagger/__init__.py"
  }
  source {
    content  = file("${local.lambdas_root}/auto-tagger/handler.py")
    filename = "lambdas/auto_tagger/handler.py"
  }
  source {
    content  = file("${local.lambdas_root}/shared/__init__.py")
    filename = "lambdas/shared/__init__.py"
  }
  source {
    content  = file("${local.lambdas_root}/shared/constants.py")
    filename = "lambdas/shared/constants.py"
  }
  source {
    content  = file("${local.lambdas_root}/shared/utils.py")
    filename = "lambdas/shared/utils.py"
  }
}

resource "aws_lambda_function" "auto_tagger" {
  function_name    = local.auto_tagger_name
  role             = aws_iam_role.auto_tagger.arn
  runtime          = local.lambda_runtime
  handler          = "lambdas.auto_tagger.handler.lambda_handler"
  filename         = data.archive_file.auto_tagger.output_path
  source_code_hash = data.archive_file.auto_tagger.output_base64sha256
  timeout          = 60
  memory_size      = 256

  reserved_concurrent_executions = 10

  environment {
    variables = {
      DRY_RUN             = var.dry_run ? "true" : "false"
      GOVERNANCE_TABLE    = local.governance_table_name
      DEFAULT_ENVIRONMENT = var.environment
      DEFAULT_SQUAD       = var.default_squad
      DEFAULT_COST_CENTER = var.default_cost_center
      DEFAULT_OWNER       = var.default_owner
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.auto_tagger,
    aws_iam_role_policy.auto_tagger_logs,
  ]

  tags = { Name = local.auto_tagger_name }
}

# ── compliance-evaluator ──────────────────────────────────────────────────────

data "archive_file" "compliance_evaluator" {
  type        = "zip"
  output_path = "${path.module}/.builds/compliance_evaluator.zip"

  source {
    content  = file("${local.lambdas_root}/__init__.py")
    filename = "lambdas/__init__.py"
  }
  source {
    content  = file("${local.lambdas_root}/compliance-evaluator/__init__.py")
    filename = "lambdas/compliance_evaluator/__init__.py"
  }
  source {
    content  = file("${local.lambdas_root}/compliance-evaluator/handler.py")
    filename = "lambdas/compliance_evaluator/handler.py"
  }
  source {
    content  = file("${local.lambdas_root}/shared/__init__.py")
    filename = "lambdas/shared/__init__.py"
  }
  source {
    content  = file("${local.lambdas_root}/shared/constants.py")
    filename = "lambdas/shared/constants.py"
  }
  source {
    content  = file("${local.lambdas_root}/shared/utils.py")
    filename = "lambdas/shared/utils.py"
  }
}

resource "aws_lambda_function" "compliance_evaluator" {
  function_name    = local.compliance_evaluator_name
  role             = aws_iam_role.compliance_evaluator.arn
  runtime          = local.lambda_runtime
  handler          = "lambdas.compliance_evaluator.handler.lambda_handler"
  filename         = data.archive_file.compliance_evaluator.output_path
  source_code_hash = data.archive_file.compliance_evaluator.output_base64sha256
  timeout          = 120
  memory_size      = 256

  reserved_concurrent_executions = 10

  environment {
    variables = {
      DRY_RUN            = var.dry_run ? "true" : "false"
      GOVERNANCE_TABLE   = local.governance_table_name
      SNS_TOPIC_ARN      = aws_sns_topic.governance_alerts.arn
      SLACK_WEBHOOK_URL  = var.slack_webhook_url
      SCHEDULER_ROLE_ARN = aws_iam_role.scheduler_execution.arn
      # AWS_LAMBDA_FUNCTION_ARN is not set automatically by the runtime;
      # computed from known values to avoid a circular resource reference.
      AWS_LAMBDA_FUNCTION_ARN = local.compliance_evaluator_arn
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.compliance_evaluator,
    aws_iam_role_policy.compliance_evaluator_logs,
  ]

  tags = { Name = local.compliance_evaluator_name }
}

# ── feedback-api ──────────────────────────────────────────────────────────────

data "archive_file" "feedback_api" {
  type        = "zip"
  output_path = "${path.module}/.builds/feedback_api.zip"

  source {
    content  = file("${local.lambdas_root}/__init__.py")
    filename = "lambdas/__init__.py"
  }
  source {
    content  = file("${local.lambdas_root}/feedback-api/__init__.py")
    filename = "lambdas/feedback_api/__init__.py"
  }
  source {
    content  = file("${local.lambdas_root}/feedback-api/handler.py")
    filename = "lambdas/feedback_api/handler.py"
  }
  source {
    content  = file("${local.lambdas_root}/shared/__init__.py")
    filename = "lambdas/shared/__init__.py"
  }
  source {
    content  = file("${local.lambdas_root}/shared/constants.py")
    filename = "lambdas/shared/constants.py"
  }
  source {
    content  = file("${local.lambdas_root}/shared/utils.py")
    filename = "lambdas/shared/utils.py"
  }
}

resource "aws_lambda_function" "feedback_api" {
  function_name    = local.feedback_api_name
  role             = aws_iam_role.feedback_api.arn
  runtime          = local.lambda_runtime
  handler          = "lambdas.feedback_api.handler.lambda_handler"
  filename         = data.archive_file.feedback_api.output_path
  source_code_hash = data.archive_file.feedback_api.output_base64sha256
  timeout          = 30
  memory_size      = 128

  reserved_concurrent_executions = 5

  environment {
    variables = {
      DRY_RUN          = var.dry_run ? "true" : "false"
      GOVERNANCE_TABLE = local.governance_table_name
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.feedback_api,
    aws_iam_role_policy.feedback_api_logs,
  ]

  tags = { Name = local.feedback_api_name }
}
