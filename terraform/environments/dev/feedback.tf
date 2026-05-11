# ========================================
# FEEDBACK LAMBDA
# Reçoit les actions propriétaires via les
# liens inclus dans les emails de gouvernance.
#
# ⚠️  dry_run = true  → simulation, aucune modification AWS
#     dry_run = false → tags appliqués réellement
#
# La feedback_url est passée à la cleanup lambda
# pour construire les liens dans les emails SNS.
# ========================================

module "feedback" {
  source = "../../modules/feedback-lambda"

  environment = "dev"
  aws_region  = "eu-west-1"

  # MODE SÉCURISÉ : simulation par défaut
  dry_run = true

  dynamodb_table_arn = module.governance_state.table_arn

  # ⚠️  Changez cette valeur avant le premier déploiement
  # Générez un secret fort : openssl rand -hex 32
  feedback_secret = var.feedback_secret

  log_retention_days = 7
}

# ========================================
# OUTPUTS
# ========================================

output "feedback_lambda_name" {
  description = "Nom de la Lambda feedback"
  value       = module.feedback.lambda_function_name
}

output "feedback_url" {
  description = "URL publique de la Lambda feedback"
  value       = module.feedback.feedback_url
}

output "feedback_mode" {
  description = "Mode de fonctionnement actuel"
  value       = module.feedback.dry_run_mode
}
