# ========================================
# DÉPLOIEMENT DE LA LAMBDA DE CLEANUP
# ========================================

module "cleanup_lambda" {
  source = "../../modules/cleanup-lambda"

  environment = "dev"
  aws_region  = "eu-west-1"

  grace_period_hours = 24

  # MODE IMPORTANT : Simulation par défaut !
  # Changez à false pour activer la suppression réelle
  dry_run = true

  # Email pour recevoir les rapports
  notification_email = "votre.email@entreprise.com" # ← CHANGEZ CETTE VALEUR

  # Planification : tous les jours à 2h du matin
  enable_schedule     = true
  schedule_expression = "cron(0 2 * * ? *)"

  # Rétention des logs : 7 jours
  log_retention_days = 7
}

# ========================================
# OUTPUTS
# ========================================

output "cleanup_lambda_name" {
  description = "Nom de la Lambda de cleanup"
  value       = module.cleanup_lambda.lambda_function_name
}

output "cleanup_mode" {
  description = "Mode de fonctionnement actuel"
  value       = module.cleanup_lambda.dry_run_mode
}

output "cleanup_schedule" {
  description = "Planification d'exécution"
  value       = module.cleanup_lambda.schedule_expression
}
