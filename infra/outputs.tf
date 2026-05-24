output "feedback_api_url" {
  description = "Base URL for the governance feedback API (append /feedback for the endpoint)"
  value       = aws_apigatewayv2_stage.default.invoke_url
}

output "governance_table_name" {
  description = "DynamoDB governance events table name (pass as GOVERNANCE_TABLE env var)"
  value       = aws_dynamodb_table.governance_events.name
}

output "governance_table_arn" {
  description = "DynamoDB governance events table ARN"
  value       = aws_dynamodb_table.governance_events.arn
}

output "sns_topic_arn" {
  description = "SNS topic ARN for governance alerts (check your email to confirm subscription)"
  value       = aws_sns_topic.governance_alerts.arn
}

output "kms_key_arn" {
  description = "KMS key ARN used to encrypt DynamoDB, CloudWatch Logs, and SNS"
  value       = aws_kms_key.governance.arn
}

output "auto_tagger_function_arn" {
  value = aws_lambda_function.auto_tagger.arn
}

output "compliance_evaluator_function_arn" {
  value = aws_lambda_function.compliance_evaluator.arn
}

output "feedback_api_function_arn" {
  value = aws_lambda_function.feedback_api.arn
}

output "scheduler_role_arn" {
  description = "IAM role assumed by EventBridge Scheduler when invoking compliance-evaluator"
  value       = aws_iam_role.scheduler_execution.arn
}
