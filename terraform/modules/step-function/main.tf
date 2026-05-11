locals {
  name_prefix = "tagging-governance-${var.environment}"

  # Chemins des zips Lambda
  notify_zip   = "${path.module}/notify.zip"
  check_zip    = "${path.module}/check.zip"
  remediate_zip = "${path.module}/remediate.zip"
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

# ========================================
# ARCHIVES LAMBDA
# ========================================

data "archive_file" "notify" {
  type        = "zip"
  source_file = "${path.module}/../../../lambda/step_function/notify.py"
  output_path = local.notify_zip
}

data "archive_file" "check" {
  type        = "zip"
  source_file = "${path.module}/../../../lambda/step_function/check_compliance.py"
  output_path = local.check_zip
}

data "archive_file" "remediate" {
  type        = "zip"
  source_file = "${path.module}/../../../lambda/step_function/remediate_resource.py"
  output_path = local.remediate_zip
}

# ========================================
# IAM — RÔLE PARTAGÉ POUR LES 3 LAMBDAS
# ========================================

resource "aws_iam_role" "lambda_role" {
  name = "${local.name_prefix}-sfn-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = {
    Name        = "${local.name_prefix}-sfn-lambda-role"
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

resource "aws_iam_role_policy" "lambda_policy" {
  name = "${local.name_prefix}-sfn-lambda-policy"
  role = aws_iam_role.lambda_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Lecture/écriture DynamoDB (taskToken + compliance check)
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem", "dynamodb:GetItem",
          "dynamodb:Query",   "dynamodb:UpdateItem",
        ]
        Resource = var.dynamodb_table_arn
      },
      {
        # SNS pour les notifications
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = var.sns_topic_arn
      },
      {
        # Remédiation des ressources (stop + quarantine, jamais delete)
        Effect = "Allow"
        Action = [
          "ec2:StopInstances",
          "ec2:CreateTags",
          "rds:StopDBInstance",
          "rds:AddTagsToResource",
          "rds:DescribeDBInstances",
          "s3:PutBucketTagging",
          "s3:GetBucketTagging",
          "s3:PutPublicAccessBlock",
          "lambda:PutFunctionConcurrency",
          "lambda:TagResource",
          "lambda:GetFunction",
        ]
        Resource = "*"
      },
      {
        # SendTaskSuccess / SendTaskFailure pour reprendre la Step Function
        Effect   = "Allow"
        Action   = ["states:SendTaskSuccess", "states:SendTaskFailure"]
        Resource = "*"
      }
    ]
  })
}

# ========================================
# LAMBDA — NOTIFY
# ========================================

resource "aws_lambda_function" "notify" {
  filename         = local.notify_zip
  function_name    = "${local.name_prefix}-notify"
  role             = aws_iam_role.lambda_role.arn
  handler          = "notify.lambda_handler"
  source_code_hash = data.archive_file.notify.output_base64sha256
  runtime          = "python3.12"
  architectures    = ["arm64"]
  timeout          = 30
  memory_size      = 128

  environment {
    variables = {
      SNS_TOPIC_ARN       = var.sns_topic_arn
      DYNAMODB_TABLE_NAME = split(":", var.dynamodb_table_arn)[6]
      FEEDBACK_URL        = var.feedback_url
      FEEDBACK_SECRET     = var.feedback_secret
      SLACK_WEBHOOK_URL   = var.slack_webhook_url
    }
  }

  tags = {
    Name        = "${local.name_prefix}-notify"
    ManagedBy   = "Terraform"
    Environment = var.environment
    Owner       = "CloudGovernance"
    Squad       = "Platform"
    CostCenter  = "INFRA"
  }
}

resource "aws_cloudwatch_log_group" "notify_logs" {
  name              = "/aws/lambda/${aws_lambda_function.notify.function_name}"
  retention_in_days = var.log_retention_days
}

# ========================================
# LAMBDA — CHECK COMPLIANCE
# ========================================

resource "aws_lambda_function" "check" {
  filename         = local.check_zip
  function_name    = "${local.name_prefix}-check"
  role             = aws_iam_role.lambda_role.arn
  handler          = "check_compliance.lambda_handler"
  source_code_hash = data.archive_file.check.output_base64sha256
  runtime          = "python3.12"
  architectures    = ["arm64"]
  timeout          = 30
  memory_size      = 128

  environment {
    variables = {
      DYNAMODB_TABLE_NAME = split(":", var.dynamodb_table_arn)[6]
    }
  }

  tags = {
    Name        = "${local.name_prefix}-check"
    ManagedBy   = "Terraform"
    Environment = var.environment
    Owner       = "CloudGovernance"
    Squad       = "Platform"
    CostCenter  = "INFRA"
  }
}

resource "aws_cloudwatch_log_group" "check_logs" {
  name              = "/aws/lambda/${aws_lambda_function.check.function_name}"
  retention_in_days = var.log_retention_days
}

# ========================================
# LAMBDA — REMEDIATE RESOURCE
# ========================================

resource "aws_lambda_function" "remediate" {
  filename         = local.remediate_zip
  function_name    = "${local.name_prefix}-remediate"
  role             = aws_iam_role.lambda_role.arn
  handler          = "remediate_resource.lambda_handler"
  source_code_hash = data.archive_file.remediate.output_base64sha256
  runtime          = "python3.12"
  architectures    = ["arm64"]
  timeout          = 120
  memory_size      = 128

  environment {
    variables = {
      DRY_RUN             = var.dry_run ? "true" : "false"
      AWS_REGION          = data.aws_region.current.name
      DYNAMODB_TABLE_NAME = split(":", var.dynamodb_table_arn)[6]
    }
  }

  tags = {
    Name        = "${local.name_prefix}-remediate"
    ManagedBy   = "Terraform"
    Environment = var.environment
    Owner       = "CloudGovernance"
    Squad       = "Platform"
    CostCenter  = "INFRA"
  }
}

resource "aws_cloudwatch_log_group" "remediate_logs" {
  name              = "/aws/lambda/${aws_lambda_function.remediate.function_name}"
  retention_in_days = var.log_retention_days
}

# ========================================
# IAM — RÔLE STEP FUNCTION
# ========================================

resource "aws_iam_role" "sfn_role" {
  name = "sfn-${local.name_prefix}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "sfn_policy" {
  name = "sfn-${local.name_prefix}-policy"
  role = aws_iam_role.sfn_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["lambda:InvokeFunction"]
        Resource = [
          aws_lambda_function.notify.arn,
          aws_lambda_function.check.arn,
          aws_lambda_function.remediate.arn,
        ]
      },
      {
        # Nécessaire pour waitForTaskToken : la SF doit pouvoir écrire des logs
        Effect   = "Allow"
        Action   = ["logs:CreateLogDelivery", "logs:GetLogDelivery",
                    "logs:UpdateLogDelivery", "logs:DeleteLogDelivery",
                    "logs:ListLogDeliveries", "logs:PutResourcePolicy",
                    "logs:DescribeResourcePolicies", "logs:DescribeLogGroups"]
        Resource = "*"
      }
    ]
  })
}

# ========================================
# STATE MACHINE
# ========================================

resource "aws_sfn_state_machine" "tagging_governance" {
  name     = local.name_prefix
  role_arn = aws_iam_role.sfn_role.arn
  type     = "STANDARD"

  # Charge l'ASL depuis le fichier JSON et substitue les ARN Lambda
  definition = templatefile("${path.module}/state_machine.asl.json", {
    NotifyLambdaArn   = aws_lambda_function.notify.arn
    CheckLambdaArn    = aws_lambda_function.check.arn
    RemediateLambdaArn = aws_lambda_function.remediate.arn
  })

  tags = {
    Name        = local.name_prefix
    Environment = var.environment
    ManagedBy   = "Terraform"
    Owner       = "CloudGovernance"
    Squad       = "Platform"
    CostCenter  = "INFRA"
  }
}
