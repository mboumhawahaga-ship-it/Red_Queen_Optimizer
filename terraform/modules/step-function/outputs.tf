output "state_machine_arn" {
  description = "ARN de la State Machine Step Functions"
  value       = aws_sfn_state_machine.tagging_governance.arn
}

output "state_machine_name" {
  description = "Nom de la State Machine Step Functions"
  value       = aws_sfn_state_machine.tagging_governance.name
}

output "sfn_role_arn" {
  description = "ARN du rôle IAM utilisé par la State Machine"
  value       = aws_iam_role.sfn_role.arn
}
