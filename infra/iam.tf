# ── Shared trust policies ─────────────────────────────────────────────────────

locals {
  lambda_trust_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  scheduler_trust_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  # CloudWatch Logs policy shared by all three Lambda roles.
  # Log group must be pre-created (see observability.tf) so only stream/event write is needed.
  logs_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "logs:CreateLogStream",
        "logs:PutLogEvents",
      ]
      Resource = "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${local.name_prefix}-*:*"
    }]
  })

  # DynamoDB access shared by all three Lambda roles.
  dynamodb_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:GetItem",
          "dynamodb:Query",
        ]
        Resource = aws_dynamodb_table.governance_events.arn
      },
      # Lambda reads encrypted DynamoDB items — KMS Decrypt is required for CMK tables.
      {
        Effect   = "Allow"
        Action   = ["kms:GenerateDataKey*", "kms:Decrypt"]
        Resource = aws_kms_key.governance.arn
      },
    ]
  })
}

# ── auto-tagger ───────────────────────────────────────────────────────────────

resource "aws_iam_role" "auto_tagger" {
  name               = "${local.name_prefix}-auto-tagger"
  assume_role_policy = local.lambda_trust_policy

  tags = { Name = "${local.name_prefix}-auto-tagger" }
}

resource "aws_iam_role_policy" "auto_tagger_logs" {
  name   = "cloudwatch-logs"
  role   = aws_iam_role.auto_tagger.id
  policy = local.logs_policy
}

resource "aws_iam_role_policy" "auto_tagger_dynamodb" {
  name   = "dynamodb"
  role   = aws_iam_role.auto_tagger.id
  policy = local.dynamodb_policy
}

resource "aws_iam_role_policy" "auto_tagger_tagging" {
  name = "resource-tagging"
  role = aws_iam_role.auto_tagger.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EC2TagInstances"
        Effect = "Allow"
        Action = [
          "ec2:CreateTags",
          "ec2:DescribeTags",
          "ec2:DescribeInstances",
        ]
        Resource = [
          "arn:aws:ec2:*:${data.aws_caller_identity.current.account_id}:instance/*",
          "arn:aws:ec2:*:${data.aws_caller_identity.current.account_id}:volume/*",
        ]
      },
      {
        Sid    = "EC2DescribeInstances"
        Effect = "Allow"
        Action = ["ec2:DescribeInstances", "ec2:DescribeTags"]
        # Describe actions do not support resource-level restrictions.
        Resource = "*"
      },
      {
        Sid      = "S3Tagging"
        Effect   = "Allow"
        Action   = ["s3:GetBucketTagging", "s3:PutBucketTagging"]
        Resource = "arn:aws:s3:::*"
      },
      {
        Sid    = "RDSTagging"
        Effect = "Allow"
        Action = [
          "rds:AddTagsToResource",
          "rds:ListTagsForResource",
          "rds:DescribeDBInstances",
        ]
        Resource = "arn:aws:rds:*:${data.aws_caller_identity.current.account_id}:db:*"
      },
      {
        Sid    = "LambdaTagging"
        Effect = "Allow"
        Action = [
          "lambda:ListTags",
          "lambda:TagResource",
          "lambda:GetFunction",
        ]
        Resource = "arn:aws:lambda:*:${data.aws_caller_identity.current.account_id}:function:*"
      },
    ]
  })
}

# ── compliance-evaluator ──────────────────────────────────────────────────────

resource "aws_iam_role" "compliance_evaluator" {
  name               = "${local.name_prefix}-compliance-evaluator"
  assume_role_policy = local.lambda_trust_policy

  tags = { Name = "${local.name_prefix}-compliance-evaluator" }
}

resource "aws_iam_role_policy" "compliance_evaluator_logs" {
  name   = "cloudwatch-logs"
  role   = aws_iam_role.compliance_evaluator.id
  policy = local.logs_policy
}

resource "aws_iam_role_policy" "compliance_evaluator_dynamodb" {
  name   = "dynamodb"
  role   = aws_iam_role.compliance_evaluator.id
  policy = local.dynamodb_policy
}

resource "aws_iam_role_policy" "compliance_evaluator_operations" {
  name = "governance-operations"
  role = aws_iam_role.compliance_evaluator.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "SNSPublish"
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = aws_sns_topic.governance_alerts.arn
      },
      {
        Sid    = "SchedulerManage"
        Effect = "Allow"
        Action = [
          "scheduler:CreateSchedule",
          "scheduler:DeleteSchedule",
        ]
        # Schedules are created dynamically per resource — wildcard on schedule name is required.
        Resource = "arn:aws:scheduler:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:schedule/default/*"
      },
      {
        Sid      = "PassSchedulerRole"
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = aws_iam_role.scheduler_execution.arn
        Condition = {
          StringEquals = {
            "iam:PassedToService" = "scheduler.amazonaws.com"
          }
        }
      },
      # Read resource tags to decide compliance status.
      {
        Sid    = "DescribeResources"
        Effect = "Allow"
        Action = [
          "ec2:DescribeInstances",
          "ec2:DescribeTags",
          "s3:GetBucketTagging",
          "rds:DescribeDBInstances",
          "rds:ListTagsForResource",
          "lambda:GetFunction",
          "lambda:ListTags",
        ]
        Resource = "*"
      },
      # Apply quarantine tags when SLA expires.
      {
        Sid    = "QuarantineTags"
        Effect = "Allow"
        Action = [
          "ec2:CreateTags",
          "s3:PutBucketTagging",
          "rds:AddTagsToResource",
          "lambda:TagResource",
        ]
        Resource = "*"
      },
    ]
  })
}

# ── feedback-api ──────────────────────────────────────────────────────────────

resource "aws_iam_role" "feedback_api" {
  name               = "${local.name_prefix}-feedback-api"
  assume_role_policy = local.lambda_trust_policy

  tags = { Name = "${local.name_prefix}-feedback-api" }
}

resource "aws_iam_role_policy" "feedback_api_logs" {
  name   = "cloudwatch-logs"
  role   = aws_iam_role.feedback_api.id
  policy = local.logs_policy
}

resource "aws_iam_role_policy" "feedback_api_dynamodb" {
  name   = "dynamodb"
  role   = aws_iam_role.feedback_api.id
  policy = local.dynamodb_policy
}

# ── EventBridge Scheduler execution role ─────────────────────────────────────
# Assumed by EventBridge Scheduler to invoke compliance-evaluator at SLA deadline.

resource "aws_iam_role" "scheduler_execution" {
  name               = "${local.name_prefix}-scheduler-execution"
  assume_role_policy = local.scheduler_trust_policy

  tags = { Name = "${local.name_prefix}-scheduler-execution" }
}

resource "aws_iam_role_policy" "scheduler_invoke_lambda" {
  name = "invoke-compliance-evaluator"
  role = aws_iam_role.scheduler_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["lambda:InvokeFunction"]
      # Pre-computed ARN avoids circular dependency with aws_lambda_function.compliance_evaluator
      Resource = local.compliance_evaluator_arn
    }]
  })
}
