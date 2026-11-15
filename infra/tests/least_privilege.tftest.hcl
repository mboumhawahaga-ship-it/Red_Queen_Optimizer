# All providers are mocked. "apply" here evaluates synthetic resources only;
# it never uses an AWS account or creates real infrastructure. Terraform >= 1.7.
mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_region" {
    defaults = { name = "eu-west-1" }
  }
  mock_resource "aws_kms_key" {
    defaults = { arn = "arn:aws:kms:eu-west-1:123456789012:key/00000000-0000-0000-0000-000000000001" }
  }
  mock_resource "aws_dynamodb_table" {
    defaults = { arn = "arn:aws:dynamodb:eu-west-1:123456789012:table/red-queen-dev-governance-events" }
  }
  mock_resource "aws_sns_topic" {
    defaults = { arn = "arn:aws:sns:eu-west-1:123456789012:red-queen-dev-governance-alerts" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/mock-runtime" }
  }
  mock_resource "aws_lambda_function" {
    defaults = {
      arn        = "arn:aws:lambda:eu-west-1:123456789012:function:mock-function"
      invoke_arn = "arn:aws:apigateway:eu-west-1:lambda:path/2015-03-31/functions/arn:aws:lambda:eu-west-1:123456789012:function:mock-function/invocations"
    }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:eu-west-1:123456789012:log-group:/aws/lambda/mock" }
  }
  mock_resource "aws_apigatewayv2_api" {
    defaults = { execution_arn = "arn:aws:execute-api:eu-west-1:123456789012:mockapi" }
  }
  mock_resource "aws_cloudwatch_event_rule" {
    defaults = { arn = "arn:aws:events:eu-west-1:123456789012:rule/mock-rule" }
  }
}

mock_provider "archive" {}

variables {
  sns_email = "offline@example.com"
}

run "empty_scope_grants_no_workload_access" {
  command = apply

  assert {
    condition = (length(aws_iam_role_policy.auto_tagger_tagging) == 0
    )
    error_message = "An empty scope must not create a workload policy for auto-tagger."
  }
  assert {
    condition = (alltrue([
      for statement in jsondecode(aws_iam_role_policy.compliance_evaluator_operations.policy).Statement :
      !anytrue([for action in statement.Action : contains([
        "ec2:CreateTags", "s3:PutBucketTagging", "rds:AddTagsToResource", "lambda:TagResource",
        "ec2:DescribeTags", "lambda:GetFunctionConfiguration", "s3:GetBucketTagging",
      ], action)])
      ])
    )
    error_message = "An empty scope must not grant workload read/tag operations."
  }
}

run "scoped_permissions_and_separation" {
  command = apply
  variables {
    governance_scope = {
      ec2_instance_arns    = ["arn:aws:ec2:eu-west-1:123456789012:instance/i-0123456789abcdef0"]
      s3_bucket_arns       = ["arn:aws:s3:::rq-customer-demo"]
      rds_db_arns          = ["arn:aws:rds:eu-west-1:123456789012:db:customer-demo"]
      lambda_function_arns = ["arn:aws:lambda:eu-west-1:123456789012:function:customer-demo"]
    }
  }

  assert {
    condition = (alltrue([
      for statement in concat(
        jsondecode(aws_iam_role_policy.auto_tagger_tagging[0].policy).Statement,
        jsondecode(aws_iam_role_policy.compliance_evaluator_operations.policy).Statement,
      ) : statement.Resource != "*" || contains(["ReadEC2Metadata", "PublishGovernanceMetrics"], statement.Sid)
      ])
    )
    error_message = "Only EC2 Describe and PutMetricData may require Resource=*."
  }
  assert {
    condition = (alltrue([
      for statement in jsondecode(aws_iam_role_policy.auto_tagger_tagging[0].policy).Statement :
      toset(statement.Condition["ForAllValues:StringEquals"]["aws:TagKeys"]) == toset(["Owner", "Squad", "CostCenter", "Environment"])
      && statement.Condition.Null["aws:TagKeys"] == "false"
      if contains(["TagEC2Instances", "TagDatabaseInstances", "TagFunctions"], statement.Sid)
      ])
    )
    error_message = "Auto-tagger must only write the four attribution keys, with a nonempty tag set."
  }
  assert {
    condition = (alltrue([
      for statement in jsondecode(aws_iam_role_policy.compliance_evaluator_operations.policy).Statement :
      toset(statement.Condition["ForAllValues:StringEquals"]["aws:TagKeys"]) == toset(["Status", "QuarantinedAt", "QuarantineReason"])
      && statement.Condition.Null["aws:TagKeys"] == "false"
      if contains(["TagEC2Instances", "TagDatabaseInstances", "TagFunctions"], statement.Sid)
      ])
    )
    error_message = "Evaluator must only write the three quarantine keys on additive tag APIs."
  }
  assert {
    condition = (alltrue([
      for statement in concat(
        jsondecode(aws_iam_role_policy.auto_tagger_tagging[0].policy).Statement,
        jsondecode(aws_iam_role_policy.compliance_evaluator_operations.policy).Statement,
      ) : statement.Resource == ["arn:aws:s3:::rq-customer-demo"]
      && statement.Condition.StringEquals["aws:ResourceAccount"] == "123456789012"
      && !can(statement.Condition["ForAllValues:StringEquals"])
      if contains(["ReadBucketTags", "ReplaceAllowedBucketTags"], statement.Sid)
      ])
    )
    error_message = "S3 must be limited to allowed buckets owned by this account, preserving other tag keys."
  }
  assert {
    condition = (alltrue([
      for statement in jsondecode(aws_iam_role_policy.auto_tagger_tagging[0].policy).Statement :
      !contains(statement.Action, "lambda:GetFunction")
      && !contains(statement.Action, "ec2:DescribeTags")
      && !can(regex("volume/", jsonencode(statement.Resource)))
      ])
    )
    error_message = "Unused reads, Lambda code download and EBS tagging must not be granted."
  }
  assert {
    condition = (jsondecode(aws_iam_role_policy.auto_tagger_dynamodb.policy).Statement[0].Action == ["dynamodb:PutItem"]
      && toset(jsondecode(aws_iam_role_policy.feedback_api_dynamodb.policy).Statement[0].Action) == toset(["dynamodb:PutItem", "dynamodb:Query"])
      && jsondecode(aws_iam_role_policy.feedback_api_dynamodb.policy).Statement[1].Action == ["kms:Decrypt"]
    )
    error_message = "Each role must only have the DynamoDB APIs it uses and constrained decryption."
  }
  assert {
    condition = (alltrue([for policy in values(local.logs_policies) :
      length(jsondecode(policy).Statement) == 1
      && length(jsondecode(policy).Statement[0].Action) == 2
      && !can(regex("red-queen-dev-\\*", jsondecode(policy).Statement[0].Resource))
      ])
    )
    error_message = "Log permissions must be specific to each Lambda log group."
  }
}

run "scheduler_kms_and_invocation_boundaries" {
  command = apply
  assert {
    condition = (jsondecode(aws_iam_role.scheduler_execution.assume_role_policy).Statement[0].Condition.ArnEquals["aws:SourceArn"] == "arn:aws:scheduler:eu-west-1:123456789012:schedule-group/red-queen-dev-sla"
      && jsondecode(aws_iam_role.scheduler_execution.assume_role_policy).Statement[0].Condition.StringEquals["aws:SourceAccount"] == "123456789012"
      && jsondecode(aws_iam_role_policy.scheduler_invoke_lambda.policy).Statement[0].Resource == local.compliance_evaluator_arn
    )
    error_message = "Scheduler must trust exactly the project group/account and invoke only the evaluator."
  }
  assert {
    condition = (alltrue([
      for statement in jsondecode(aws_iam_role_policy.compliance_evaluator_operations.policy).Statement :
      statement.Resource == "arn:aws:scheduler:eu-west-1:123456789012:schedule/red-queen-dev-sla/rq-sla-*"
      if statement.Sid == "SchedulerManage"
      ])
    )
    error_message = "Evaluator must not manage other schedules or groups."
  }
  assert {
    condition = (alltrue([
      for policy in values(local.dynamodb_policies) :
      jsondecode(policy).Statement[1].Condition.StringEquals["kms:ViaService"] == "dynamodb.eu-west-1.amazonaws.com"
      && jsondecode(policy).Statement[1].Condition.StringEquals["kms:EncryptionContext:aws:dynamodb:tableName"] == local.governance_table_name
      ])
    )
    error_message = "DynamoDB KMS access must require this service, table and account."
  }
  assert {
    condition = (length(jsondecode(aws_kms_key.governance.policy).Statement[1].Condition.ArnEquals["kms:EncryptionContext:aws:logs:arn"]) == 4
      && jsondecode(aws_kms_key.governance.policy).Statement[2].Condition.StringEquals["kms:EncryptionContext:aws:sns:topicArn"] == local.sns_topic_arn
    )
    error_message = "KMS service access must be bound to the four log groups and the SNS topic."
  }
  assert {
    condition = (aws_lambda_permission.feedback_api_apigw["GET"].source_arn == "arn:aws:execute-api:eu-west-1:123456789012:mockapi/$default/GET/feedback"
      && aws_lambda_permission.feedback_api_apigw["POST"].source_account == "123456789012"
      && aws_lambda_permission.auto_tagger_eventbridge.source_account == "123456789012"
    )
    error_message = "Invocation policies must constrain account, stage, method and path."
  }
}

run "reject_cross_account_workloads" {
  command = plan
  variables {
    governance_scope = { ec2_instance_arns = ["arn:aws:ec2:eu-west-1:999999999999:instance/i-0123456789abcdef0"] }
  }
  expect_failures = [aws_iam_role_policy.auto_tagger_tagging, aws_iam_role_policy.compliance_evaluator_operations]
}

run "reject_cross_region_workloads" {
  command = plan
  variables {
    governance_scope = { rds_db_arns = ["arn:aws:rds:us-east-1:123456789012:db:customer-demo"] }
  }
  expect_failures = [aws_iam_role_policy.auto_tagger_tagging, aws_iam_role_policy.compliance_evaluator_operations]
}

run "reject_global_s3_wildcard" {
  command = plan
  variables {
    governance_scope = { s3_bucket_arns = ["arn:aws:s3:::*"] }
  }
  expect_failures = [var.governance_scope]
}

run "reject_wrong_resource_type" {
  command = plan
  variables {
    governance_scope = { ec2_instance_arns = ["arn:aws:ec2:eu-west-1:123456789012:volume/vol-0123456789abcdef0"] }
  }
  expect_failures = [var.governance_scope]
}
