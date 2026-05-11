locals {
  lambda_name = "${var.environment}-auto-tagger"
  lambda_zip  = "${path.module}/lambda_function.zip"
}

data "aws_caller_identity" "current" {}

# ========================================
# ARCHIVE DU CODE
# ========================================

data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../../../lambda/auto_tagger"
  output_path = local.lambda_zip
  excludes    = ["__pycache__", "*.pyc", ".venv", "test_*.py"]
}

# ========================================
# IAM
# ========================================

resource "aws_iam_role" "lambda_role" {
  name = "${local.lambda_name}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = {
    Name        = "${local.lambda_name}-role"
    ManagedBy   = "Terraform"
    Environment = var.environment
    Owner       = "CloudGovernance"
    Squad       = "Platform"
    CostCenter  = "INFRA"
  }
}

resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "auto_tagger_policy" {
  name = "${local.lambda_name}-policy"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Lecture des tags existants
        Effect = "Allow"
        Action = [
          "ec2:DescribeInstances",
          "ec2:DescribeTags",
          "s3:GetBucketTagging",
          "rds:ListTagsForResource",
          "lambda:ListTags",
        ]
        Resource = "*"
      },
      {
        # Écriture des tags — scoped par région/compte
        Effect = "Allow"
        Action = ["ec2:CreateTags"]
        Resource = [
          "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:instance/*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutBucketTagging"]
        Resource = ["arn:aws:s3:::*"]
      },
      {
        Effect = "Allow"
        Action = ["rds:AddTagsToResource"]
        Resource = [
          "arn:aws:rds:${var.aws_region}:${data.aws_caller_identity.current.account_id}:db:*"
        ]
      },
      {
        Effect = "Allow"
        Action = ["lambda:TagResource"]
        Resource = [
          "arn:aws:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:GetItem",
        ]
        Resource = var.dynamodb_table_arn
      }
    ]
  })
}

# ========================================
# FONCTION LAMBDA
# ========================================

resource "aws_lambda_function" "auto_tagger" {
  filename         = local.lambda_zip
  function_name    = local.lambda_name
  role             = aws_iam_role.lambda_role.arn
  handler          = "handler.lambda_handler"
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  runtime          = "python3.12"
  architectures    = ["arm64"]
  timeout          = 60
  memory_size      = 128

  environment {
    variables = {
      DRY_RUN             = var.dry_run ? "true" : "false"
      DYNAMODB_TABLE_NAME = split(":", var.dynamodb_table_arn)[6]
      DEFAULT_ENVIRONMENT = var.environment
      DEFAULT_SQUAD       = var.default_squad
      DEFAULT_COST_CENTER = var.default_cost_center
      DEFAULT_OWNER       = var.default_owner
    }
  }

  tags = {
    Name        = local.lambda_name
    ManagedBy   = "Terraform"
    Environment = var.environment
    Owner       = "CloudGovernance"
    Squad       = "Platform"
    CostCenter  = "INFRA"
  }
}

# ========================================
# CLOUDWATCH LOGS
# ========================================

resource "aws_cloudwatch_log_group" "lambda_logs" {
  name              = "/aws/lambda/${local.lambda_name}"
  retention_in_days = var.log_retention_days

  tags = {
    Name        = "${local.lambda_name}-logs"
    ManagedBy   = "Terraform"
    Environment = var.environment
  }
}

# ========================================
# EVENTBRIDGE — écoute CloudTrail
# ========================================

resource "aws_cloudwatch_event_rule" "cloudtrail_events" {
  name        = "${local.lambda_name}-trigger"
  description = "Déclenche l'auto-tagger sur création de ressources EC2/S3/RDS/Lambda"

  event_pattern = jsonencode({
    source      = ["aws.ec2", "aws.s3", "aws.rds", "aws.lambda"]
    detail-type = ["AWS API Call via CloudTrail"]
    detail = {
      eventName = [
        "RunInstances",
        "CreateBucket",
        "CreateDBInstance",
        "CreateFunction20150331",
      ]
    }
  })

  tags = {
    Name        = "${local.lambda_name}-trigger"
    ManagedBy   = "Terraform"
    Environment = var.environment
  }
}

resource "aws_cloudwatch_event_target" "lambda_target" {
  rule      = aws_cloudwatch_event_rule.cloudtrail_events.name
  target_id = "auto-tagger-lambda"
  arn       = aws_lambda_function.auto_tagger.arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.auto_tagger.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.cloudtrail_events.arn
}
