# ========================================
# DEPLOIEMENT DE LA LAMBDA DE METRIQUES
# Alimente le dashboard Grafana avec des
# metriques CloudWatch custom
# ========================================

module "metrics_lambda" {
  source = "../../modules/metrics-lambda"

  environment = "dev"
  aws_region  = "eu-west-1"

  # Planification : toutes les 6 heures
  enable_schedule     = true
  schedule_expression = "rate(6 hours)"

  # Retention des logs : 7 jours
  log_retention_days = 7
}

# ========================================
# OUTPUTS
# ========================================

output "metrics_lambda_name" {
  description = "Nom de la Lambda de metriques"
  value       = module.metrics_lambda.lambda_function_name
}

output "metrics_schedule" {
  description = "Planification d'execution des metriques"
  value       = module.metrics_lambda.schedule_expression
}
