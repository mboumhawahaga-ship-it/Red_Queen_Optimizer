module "governance_state" {
  source = "../../modules/dynamodb"

  table_name  = "redqueen-governance-state"
  environment = "dev"
  enable_pitr = false
  ttl_days    = 90
}

output "dynamodb_table_name" {
  description = "Nom de la table DynamoDB de gouvernance"
  value       = module.governance_state.table_name
}

output "dynamodb_table_arn" {
  description = "ARN de la table DynamoDB de gouvernance"
  value       = module.governance_state.table_arn
}
