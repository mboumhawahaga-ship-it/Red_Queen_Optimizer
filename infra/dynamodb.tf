resource "aws_dynamodb_table" "governance_events" {
  name         = local.governance_table_name
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

  # Records expire automatically via TTL (default: 90 days, set by shared/constants.py)
  ttl {
    attribute_name = "ttl_expiry"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = aws_kms_key.governance.arn
  }

  tags = {
    Name = local.governance_table_name
  }
}
