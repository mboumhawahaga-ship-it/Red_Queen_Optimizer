resource "aws_dynamodb_table" "governance_state" {
  name         = var.table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "resource_id"
  range_key    = "scan_timestamp"

  attribute {
    name = "resource_id"
    type = "S"
  }

  attribute {
    name = "scan_timestamp"
    type = "S"
  }

  attribute {
    name = "criticality"
    type = "S"
  }

  attribute {
    name = "environment"
    type = "S"
  }

  # GSI 1 : requêter par criticité (CRITICAL / NON_CRITICAL)
  global_secondary_index {
    name            = "criticality-index"
    hash_key        = "criticality"
    range_key       = "scan_timestamp"
    projection_type = "ALL"
  }

  # GSI 2 : requêter par environnement
  global_secondary_index {
    name            = "environment-index"
    hash_key        = "environment"
    range_key       = "scan_timestamp"
    projection_type = "ALL"
  }

  # TTL : purge automatique des entrées après 90 jours
  ttl {
    attribute_name = "ttl_expiry"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = var.enable_pitr
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name        = var.table_name
    Environment = var.environment
    ManagedBy   = "Terraform"
    Owner       = "CloudGovernance"
    Squad       = "Platform"
    CostCenter  = "INFRA"
  }
}
