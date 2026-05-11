# ========================================
# AUTO-TAGGER LAMBDA
# Applique automatiquement les tags manquants
# sur les ressources nouvellement créées.
#
# ⚠️  dry_run = true  → simulation, aucune modification AWS
#     dry_run = false → tags appliqués réellement
# ========================================

module "auto_tagger" {
  source = "../../modules/auto-tagger-lambda"

  environment = "dev"
  aws_region  = "eu-west-1"

  # MODE SÉCURISÉ : simulation par défaut
  # Passez à false uniquement après validation des logs CloudWatch
  dry_run = true

  # Table DynamoDB partagée avec le scanner
  dynamodb_table_arn = module.governance_state.table_arn

  # Valeurs par défaut appliquées si non déductibles du contexte IAM
  default_squad       = "unknown"
  default_cost_center = "CC-000"
  default_owner       = "auto-tagger@entreprise.com"

  log_retention_days = 7
}

# ========================================
# OUTPUTS
# ========================================

output "auto_tagger_lambda_name" {
  description = "Nom de la Lambda auto-tagger"
  value       = module.auto_tagger.lambda_function_name
}

output "auto_tagger_mode" {
  description = "Mode de fonctionnement actuel"
  value       = module.auto_tagger.dry_run_mode
}

output "auto_tagger_eventbridge_rule" {
  description = "ARN de la règle EventBridge"
  value       = module.auto_tagger.eventbridge_rule_arn
}
