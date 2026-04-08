locals {
  grace_seconds = var.grace_period_hours * 3600
  name_prefix   = "tagging-governance-${var.environment}"
}

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
  inline_policy {
    name = "lambda-invoke"
    policy = jsonencode({
      Version = "2012-10-17"
      Statement = [{
        Effect   = "Allow"
        Action   = "lambda:InvokeFunction"
        Resource = [var.notify_lambda_arn, var.check_lambda_arn, var.quarantine_lambda_arn]
      }]
    })
  }
}

resource "aws_sfn_state_machine" "tagging_governance" {
  name     = local.name_prefix
  role_arn = aws_iam_role.sfn_role.arn

  # 4 états : NotifyOwner → WaitGracePeriod → CheckCompliance → QuarantineResource
  # QuarantineResource STOP l'instance uniquement, pas de suppression
  definition = jsonencode({
    Comment = "AWS Tagging Governance - remédiation ressources non conformes"
    StartAt = "NotifyOwner"
    States = {
      NotifyOwner = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = { FunctionName = var.notify_lambda_arn, "Payload.$" = "$" }
        ResultPath = "$.notify_result"
        Next       = "WaitGracePeriod"
      }
      WaitGracePeriod = {
        Type    = "Wait"
        Seconds = local.grace_seconds
        Next    = "CheckCompliance"
      }
      CheckCompliance = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = { FunctionName = var.check_lambda_arn, "Payload.$" = "$" }
        ResultPath = "$.check_result"
        Next       = "QuarantineResource"
      }
      QuarantineResource = {
        Type     = "Task"
        Resource = "arn:aws:states:::lambda:invoke"
        Parameters = { FunctionName = var.quarantine_lambda_arn, "Payload.$" = "$" }
        ResultPath = "$.quarantine_result"
        End        = true
      }
    }
  })

  tags = {
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}
