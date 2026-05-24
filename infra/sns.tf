resource "aws_sns_topic" "governance_alerts" {
  name              = "${local.name_prefix}-governance-alerts"
  kms_master_key_id = aws_kms_key.governance.arn

  tags = {
    Name = "${local.name_prefix}-governance-alerts"
  }
}

# Email subscription requires manual confirmation — subscriber receives a confirmation email on first apply.
resource "aws_sns_topic_subscription" "email_alerts" {
  topic_arn = aws_sns_topic.governance_alerts.arn
  protocol  = "email"
  endpoint  = var.sns_email
}
