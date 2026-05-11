locals {
  lambda_name = "${var.environment}-feedback"
  lambda_zip  = "${path.module}/lambda_function.zip"
}

data "aws_caller_identity" "current" {}

# ========================================
# SECRET HMAC DANS SSM PARAMETER STORE
# ========================================

resource "aws_ssm_parameter" "feedback_secret" {
  name        = "/${var.environment}/redqueen/feedback-secret"
  type        = "SecureString"
  value       = var.feedback_secret
  description = "Secret HMAC pour signer les liens de feedback Red Queen"

  tags = {
    Name        = "${local.lambda_name}-secret"
    ManagedBy   = "Terraform"
    Environment = var.environment
    Owner       = "CloudGovernance"
    Squad       = "Platform"
    CostCenter  = "INFRA"
  }
}

# ========================================
# ARCHIVE DU CODE
# ========================================

data "archive_file" "lambda_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../../../lambda/feedback"
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

resource "aws_iam_role_policy" "feedback_policy" {
  name = "${local.lambda_name}-policy"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Lecture DynamoDB pour idempotence + écriture feedback
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:UpdateItem",
        ]
        Resource = var.dynamodb_table_arn
      },
      {
        # Application des tags sur les ressources
        Effect = "Allow"
        Action = [
          "ec2:CreateTags",
          "ec2:DescribeInstances",
        ]
        Resource = [
          "arn:aws:ec2:${var.aws_region}:${data.aws_caller_identity.current.account_id}:instance/*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:GetBucketTagging", "s3:PutBucketTagging"]
        Resource = ["arn:aws:s3:::*"]
      },
      {
        Effect = "Allow"
        Action = ["rds:AddTagsToResource", "rds:ListTagsForResource"]
        Resource = [
          "arn:aws:rds:${var.aws_region}:${data.aws_caller_identity.current.account_id}:db:*"
        ]
      },
      {
        Effect = "Allow"
        Action = ["lambda:TagResource", "lambda:GetFunction"]
        Resource = [
          "arn:aws:lambda:${var.aws_region}:${data.aws_caller_identity.current.account_id}:function:*"
        ]
      },
      {
        # Lecture du compte pour reconstruire les ARN RDS
        Effect   = "Allow"
        Action   = ["sts:GetCallerIdentity"]
        Resource = "*"
      }
    ]
  })
}

# ========================================
# FONCTION LAMBDA
# ========================================

resource "aws_lambda_function" "feedback" {
  filename         = local.lambda_zip
  function_name    = local.lambda_name
  role             = aws_iam_role.lambda_role.arn
  handler          = "handler.lambda_handler"
  source_code_hash = data.archive_file.lambda_zip.output_base64sha256
  runtime          = "python3.12"
  architectures    = ["arm64"]
  timeout          = 30
  memory_size      = 128

  environment {
    variables = {
      DYNAMODB_TABLE_NAME = split(":", var.dynamodb_table_arn)[6]
      FEEDBACK_SECRET     = var.feedback_secret
      DRY_RUN             = var.dry_run ? "true" : "false"
      AWS_REGION_NAME     = var.aws_region
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
# LAMBDA URL — accès public pour les liens email
# ========================================

resource "aws_lambda_function_url" "feedback_url" {
  function_name      = aws_lambda_function.feedback.function_name
  authorization_type = "NONE" # Public : les liens email n'ont pas de credentials AWS

  cors {
    allow_origins = ["*"]
    allow_methods = ["GET"]
    max_age       = 300
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
