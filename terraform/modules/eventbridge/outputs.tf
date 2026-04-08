output "event_rule_arn" {
  description = "ARN of the EventBridge rule"
  value       = aws_cloudwatch_event_rule.non_compliant.arn
}

output "event_rule_name" {
  description = "Name of the EventBridge rule"
  value       = aws_cloudwatch_event_rule.non_compliant.name
}

output "iam_role_arn" {
  description = "ARN of the IAM role used by EventBridge"
  value       = aws_iam_role.eventbridge.arn
}
