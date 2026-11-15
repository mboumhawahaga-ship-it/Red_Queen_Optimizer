# Runtime roles only: provisioning permissions belong to the deployment identity.
locals {
  account_id          = data.aws_caller_identity.current.account_id
  region              = data.aws_region.current.name
  scheduler_group     = "${local.name_prefix}-sla"
  scheduler_prefix    = "arn:aws:scheduler:${local.region}:${local.account_id}:schedule/${local.scheduler_group}/rq-sla-*"
  scheduler_group_arn = "arn:aws:scheduler:${local.region}:${local.account_id}:schedule-group/${local.scheduler_group}"
  sns_topic_arn       = "arn:aws:sns:${local.region}:${local.account_id}:${local.name_prefix}-governance-alerts"

  required_tag_keys   = ["Owner", "Squad", "CostCenter", "Environment"]
  quarantine_tag_keys = ["Status", "QuarantinedAt", "QuarantineReason"]

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
      Condition = {
        StringEquals = { "aws:SourceAccount" = local.account_id }
        ArnEquals    = { "aws:SourceArn" = local.scheduler_group_arn }
      }
    }]
  })

  # Each function can write streams in its own pre-created log group only.
  lambda_names = {
    auto_tagger          = local.auto_tagger_name
    compliance_evaluator = local.compliance_evaluator_name
    feedback_api         = local.feedback_api_name
  }
  logs_policies = { for role, name in local.lambda_names : role => jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda/${name}:log-stream:*"
    }]
  }) }

  # The table already exists. Runtime data access needs decryption, not key
  # administration, grants or data-key generation (DynamoDB maintains its grants).
  dynamodb_decrypt = {
    Sid      = "DecryptGovernanceTableOnly"
    Effect   = "Allow"
    Action   = ["kms:Decrypt"]
    Resource = aws_kms_key.governance.arn
    Condition = { StringEquals = {
      "kms:ViaService"                                  = "dynamodb.${local.region}.amazonaws.com"
      "kms:EncryptionContext:aws:dynamodb:tableName"    = local.governance_table_name
      "kms:EncryptionContext:aws:dynamodb:subscriberId" = local.account_id
    } }
  }
  dynamodb_policies = { for role, actions in {
    auto_tagger          = ["dynamodb:PutItem"]
    compliance_evaluator = ["dynamodb:PutItem", "dynamodb:Query"]
    feedback_api         = ["dynamodb:PutItem", "dynamodb:Query"]
    } : role => jsonencode({
      Version = "2012-10-17"
      Statement = [{
        Sid      = "GovernanceTableData"
        Effect   = "Allow"
        Action   = actions
        Resource = aws_dynamodb_table.governance_events.arn
      }, local.dynamodb_decrypt]
  }) }

  # Describe EC2 APIs do not support resource-level ARNs; keep just the API each
  # function actually uses, constrained to this region, only when EC2 is enabled.
  ec2_reads = { for role, action in {
    auto_tagger          = "ec2:DescribeInstances"
    compliance_evaluator = "ec2:DescribeTags"
    } : role => length(var.governance_scope.ec2_instance_arns) == 0 ? [] : [{
      Sid       = "ReadEC2Metadata"
      Effect    = "Allow"
      Action    = [action]
      Resource  = "*"
      Condition = { StringEquals = { "aws:RequestedRegion" = local.region } }
  }] }

  resource_reads = concat(
    length(var.governance_scope.s3_bucket_arns) == 0 ? [] : [{
      Sid       = "ReadBucketTags"
      Effect    = "Allow"
      Action    = ["s3:GetBucketTagging"]
      Resource  = var.governance_scope.s3_bucket_arns
      Condition = { StringEquals = { "aws:ResourceAccount" = local.account_id } }
    }],
    length(var.governance_scope.rds_db_arns) == 0 ? [] : [{
      Sid      = "ReadDatabaseTags"
      Effect   = "Allow"
      Action   = ["rds:DescribeDBInstances", "rds:ListTagsForResource"]
      Resource = var.governance_scope.rds_db_arns
    }],
    length(var.governance_scope.lambda_function_arns) == 0 ? [] : [{
      Sid    = "ReadFunctionTags"
      Effect = "Allow"
      # Configuration suffices to resolve an ARN; GetFunction also exposes code.
      Action   = ["lambda:GetFunctionConfiguration", "lambda:ListTags"]
      Resource = var.governance_scope.lambda_function_arns
    }],
  )

  workload_writes = { for role, keys in {
    auto_tagger          = local.required_tag_keys
    compliance_evaluator = local.quarantine_tag_keys
    } : role => concat(
    length(var.governance_scope.ec2_instance_arns) == 0 ? [] : [{
      Sid      = "TagEC2Instances"
      Effect   = "Allow"
      Action   = ["ec2:CreateTags"]
      Resource = var.governance_scope.ec2_instance_arns
      Condition = {
        "ForAllValues:StringEquals" = { "aws:TagKeys" = keys }
        Null                        = { "aws:TagKeys" = "false" }
      }
    }],
    length(var.governance_scope.s3_bucket_arns) == 0 ? [] : [{
      Sid      = "ReplaceAllowedBucketTags"
      Effect   = "Allow"
      Action   = ["s3:PutBucketTagging"]
      Resource = var.governance_scope.s3_bucket_arns
      # This API replaces the entire tag set. Do not apply an incompatible
      # tag-key allow-list: unrelated existing tags must be sent back as well.
      Condition = { StringEquals = { "aws:ResourceAccount" = local.account_id } }
    }],
    length(var.governance_scope.rds_db_arns) == 0 ? [] : [{
      Sid      = "TagDatabaseInstances"
      Effect   = "Allow"
      Action   = ["rds:AddTagsToResource"]
      Resource = var.governance_scope.rds_db_arns
      Condition = {
        "ForAllValues:StringEquals" = { "aws:TagKeys" = keys }
        Null                        = { "aws:TagKeys" = "false" }
      }
    }],
    length(var.governance_scope.lambda_function_arns) == 0 ? [] : [{
      Sid      = "TagFunctions"
      Effect   = "Allow"
      Action   = ["lambda:TagResource"]
      Resource = var.governance_scope.lambda_function_arns
      Condition = {
        "ForAllValues:StringEquals" = { "aws:TagKeys" = keys }
        Null                        = { "aws:TagKeys" = "false" }
      }
    }],
  ) }

  # Enforce account/region at plan time, not just in documentation.
  scope_in_deployment = alltrue(concat(
    [for arn in var.governance_scope.ec2_instance_arns : startswith(arn, "arn:aws:ec2:${local.region}:${local.account_id}:instance/")],
    [for arn in var.governance_scope.rds_db_arns : startswith(arn, "arn:aws:rds:${local.region}:${local.account_id}:db:")],
    [for arn in var.governance_scope.lambda_function_arns : startswith(arn, "arn:aws:lambda:${local.region}:${local.account_id}:function:")],
  ))
}

resource "aws_iam_role" "auto_tagger" {
  name               = "${local.name_prefix}-auto-tagger"
  assume_role_policy = local.lambda_trust_policy
  tags               = { Name = "${local.name_prefix}-auto-tagger" }
}
resource "aws_iam_role_policy" "auto_tagger_logs" {
  name   = "cloudwatch-logs"
  role   = aws_iam_role.auto_tagger.id
  policy = local.logs_policies.auto_tagger
}
resource "aws_iam_role_policy" "auto_tagger_dynamodb" {
  name   = "dynamodb"
  role   = aws_iam_role.auto_tagger.id
  policy = local.dynamodb_policies.auto_tagger
}
resource "aws_iam_role_policy" "auto_tagger_tagging" {
  count = length(concat(var.governance_scope.ec2_instance_arns, var.governance_scope.s3_bucket_arns,
  var.governance_scope.rds_db_arns, var.governance_scope.lambda_function_arns)) > 0 ? 1 : 0
  name = "resource-tagging"
  role = aws_iam_role.auto_tagger.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = concat(local.ec2_reads.auto_tagger, local.resource_reads, local.workload_writes.auto_tagger)
  })
  lifecycle {
    precondition {
      condition     = local.scope_in_deployment
      error_message = "Governed EC2/RDS/Lambda ARNs must belong to the deployment account and region."
    }
  }
}

resource "aws_iam_role" "compliance_evaluator" {
  name               = "${local.name_prefix}-compliance-evaluator"
  assume_role_policy = local.lambda_trust_policy
  tags               = { Name = "${local.name_prefix}-compliance-evaluator" }
}
resource "aws_iam_role_policy" "compliance_evaluator_logs" {
  name   = "cloudwatch-logs"
  role   = aws_iam_role.compliance_evaluator.id
  policy = local.logs_policies.compliance_evaluator
}
resource "aws_iam_role_policy" "compliance_evaluator_dynamodb" {
  name   = "dynamodb"
  role   = aws_iam_role.compliance_evaluator.id
  policy = local.dynamodb_policies.compliance_evaluator
}
resource "aws_iam_role_policy" "compliance_evaluator_operations" {
  name = "governance-operations"
  role = aws_iam_role.compliance_evaluator.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      { Sid = "SNSPublish", Effect = "Allow", Action = ["sns:Publish"], Resource = aws_sns_topic.governance_alerts.arn },
      {
        Sid      = "EncryptGovernanceNotifications"
        Effect   = "Allow"
        Action   = ["kms:GenerateDataKey", "kms:Decrypt"]
        Resource = aws_kms_key.governance.arn
        Condition = { StringEquals = {
          "kms:ViaService"                         = "sns.${local.region}.amazonaws.com"
          "kms:EncryptionContext:aws:sns:topicArn" = local.sns_topic_arn
        } }
      },
      {
        Sid      = "SchedulerManage"
        Effect   = "Allow"
        Action   = ["scheduler:CreateSchedule", "scheduler:GetSchedule", "scheduler:DeleteSchedule"]
        Resource = local.scheduler_prefix
      },
      {
        Sid       = "PassSchedulerRole"
        Effect    = "Allow"
        Action    = ["iam:PassRole"]
        Resource  = aws_iam_role.scheduler_execution.arn
        Condition = { StringEquals = { "iam:PassedToService" = "scheduler.amazonaws.com" } }
      },
      {
        Sid      = "PublishGovernanceMetrics"
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
        Condition = { StringEquals = {
          "cloudwatch:namespace" = local.metrics_namespace
          "aws:RequestedRegion"  = local.region
        } }
      },
    ], local.ec2_reads.compliance_evaluator, local.resource_reads, local.workload_writes.compliance_evaluator)
  })
  lifecycle {
    precondition {
      condition     = local.scope_in_deployment
      error_message = "Governed EC2/RDS/Lambda ARNs must belong to the deployment account and region."
    }
  }
}

resource "aws_iam_role" "feedback_api" {
  name               = "${local.name_prefix}-feedback-api"
  assume_role_policy = local.lambda_trust_policy
  tags               = { Name = "${local.name_prefix}-feedback-api" }
}
resource "aws_iam_role_policy" "feedback_api_logs" {
  name   = "cloudwatch-logs"
  role   = aws_iam_role.feedback_api.id
  policy = local.logs_policies.feedback_api
}
resource "aws_iam_role_policy" "feedback_api_dynamodb" {
  name   = "dynamodb"
  role   = aws_iam_role.feedback_api.id
  policy = local.dynamodb_policies.feedback_api
}

resource "aws_iam_role" "scheduler_execution" {
  name               = "${local.name_prefix}-scheduler-execution"
  assume_role_policy = local.scheduler_trust_policy
  tags               = { Name = "${local.name_prefix}-scheduler-execution" }
}
resource "aws_iam_role_policy" "scheduler_invoke_lambda" {
  name = "invoke-compliance-evaluator"
  role = aws_iam_role.scheduler_execution.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["lambda:InvokeFunction"], Resource = local.compliance_evaluator_arn }]
  })
}
