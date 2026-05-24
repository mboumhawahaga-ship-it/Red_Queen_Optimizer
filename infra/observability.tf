# Pre-create log groups so they are KMS-encrypted and retention-controlled before
# the first Lambda invocation. Lambda would auto-create unencrypted groups otherwise.

resource "aws_cloudwatch_log_group" "auto_tagger" {
  name              = "/aws/lambda/${local.auto_tagger_name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.governance.arn

  tags = { Name = "/aws/lambda/${local.auto_tagger_name}" }
}

resource "aws_cloudwatch_log_group" "compliance_evaluator" {
  name              = "/aws/lambda/${local.compliance_evaluator_name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.governance.arn

  tags = { Name = "/aws/lambda/${local.compliance_evaluator_name}" }
}

resource "aws_cloudwatch_log_group" "feedback_api" {
  name              = "/aws/lambda/${local.feedback_api_name}"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.governance.arn

  tags = { Name = "/aws/lambda/${local.feedback_api_name}" }
}

resource "aws_cloudwatch_log_group" "apigw" {
  name              = "/aws/apigateway/${local.name_prefix}-feedback"
  retention_in_days = var.log_retention_days
  kms_key_id        = aws_kms_key.governance.arn

  tags = { Name = "/aws/apigateway/${local.name_prefix}-feedback" }
}
