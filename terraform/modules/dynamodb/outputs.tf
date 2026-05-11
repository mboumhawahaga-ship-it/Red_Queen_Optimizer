output "table_name" {
  description = "Nom de la table DynamoDB"
  value       = aws_dynamodb_table.governance_state.name
}

output "table_arn" {
  description = "ARN de la table DynamoDB"
  value       = aws_dynamodb_table.governance_state.arn
}

output "table_id" {
  description = "ID de la table DynamoDB"
  value       = aws_dynamodb_table.governance_state.id
}
