output "lambda_function_name" {
  description = "Nom de la fonction Lambda feedback"
  value       = aws_lambda_function.feedback.function_name
}

output "lambda_function_arn" {
  description = "ARN de la fonction Lambda feedback"
  value       = aws_lambda_function.feedback.arn
}

output "feedback_url" {
  description = "URL publique de la Lambda feedback (à inclure dans les emails)"
  value       = aws_lambda_function_url.feedback_url.function_url
}

output "dry_run_mode" {
  description = "Mode de fonctionnement actuel"
  value       = var.dry_run ? "SIMULATION (DRY_RUN)" : "PRODUCTION (tags appliqués)"
}

output "cloudwatch_log_group" {
  description = "Groupe de logs CloudWatch"
  value       = aws_cloudwatch_log_group.lambda_logs.name
}
