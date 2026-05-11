output "state_machine_arn" {
  description = "ARN de la State Machine Step Functions"
  value       = aws_sfn_state_machine.tagging_governance.arn
}

output "state_machine_name" {
  description = "Nom de la State Machine"
  value       = aws_sfn_state_machine.tagging_governance.name
}

output "notify_lambda_arn" {
  description = "ARN de la Lambda notify"
  value       = aws_lambda_function.notify.arn
}

output "check_lambda_arn" {
  description = "ARN de la Lambda check_compliance"
  value       = aws_lambda_function.check.arn
}

output "remediate_lambda_arn" {
  description = "ARN de la Lambda remediate_resource"
  value       = aws_lambda_function.remediate.arn
}

output "sfn_role_arn" {
  description = "ARN du rôle IAM de la State Machine"
  value       = aws_iam_role.sfn_role.arn
}
