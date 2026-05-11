output "lambda_function_name" {
  description = "Nom de la fonction Lambda auto-tagger"
  value       = aws_lambda_function.auto_tagger.function_name
}

output "lambda_function_arn" {
  description = "ARN de la fonction Lambda auto-tagger"
  value       = aws_lambda_function.auto_tagger.arn
}

output "eventbridge_rule_arn" {
  description = "ARN de la règle EventBridge qui déclenche l'auto-tagger"
  value       = aws_cloudwatch_event_rule.cloudtrail_events.arn
}

output "dry_run_mode" {
  description = "Mode de fonctionnement actuel"
  value       = var.dry_run ? "SIMULATION (DRY_RUN)" : "PRODUCTION (tags appliqués)"
}

output "cloudwatch_log_group" {
  description = "Groupe de logs CloudWatch"
  value       = aws_cloudwatch_log_group.lambda_logs.name
}
