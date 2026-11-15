resource "aws_kms_key" "governance" {
  description             = "Encrypts Red Queen governance data: DynamoDB table, CloudWatch log groups, SNS topic"
  deletion_window_in_days = 7
  enable_key_rotation     = true

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowRootAccountFullAccess"
        Effect = "Allow"
        Principal = {
          AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"
        }
        Action   = "kms:*"
        Resource = "*"
      },
      # CloudWatch Logs must be granted directly in the key policy (IAM alone is insufficient).
      {
        Sid    = "AllowCloudWatchLogs"
        Effect = "Allow"
        Principal = {
          Service = "logs.${data.aws_region.current.name}.amazonaws.com"
        }
        Action = [
          "kms:Encrypt",
          "kms:Decrypt",
          "kms:ReEncrypt*",
          "kms:GenerateDataKey*",
          "kms:DescribeKey",
        ]
        Resource = "*"
        Condition = {
          ArnEquals = {
            "kms:EncryptionContext:aws:logs:arn" = concat(
              [for name in values(local.lambda_names) : "arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/${name}"],
              ["arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/apigateway/${local.name_prefix}-feedback"],
            )
          }
        }
      },
      # SNS calls KMS directly when publishing encrypted messages.
      {
        Sid    = "AllowSNS"
        Effect = "Allow"
        Principal = {
          Service = "sns.amazonaws.com"
        }
        Action = [
          "kms:GenerateDataKey",
          "kms:Decrypt",
        ]
        Resource = "*"
        Condition = { StringEquals = {
          "kms:EncryptionContext:aws:sns:topicArn" = local.sns_topic_arn
        } }
      },
    ]
  })

  tags = {
    Name = "${local.name_prefix}-governance"
  }
}

resource "aws_kms_alias" "governance" {
  name          = "alias/${local.name_prefix}-governance"
  target_key_id = aws_kms_key.governance.key_id
}
