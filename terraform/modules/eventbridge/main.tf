resource "aws_cloudwatch_event_rule" "non_compliant" {
  name        = "${var.environment}-config-non-compliant"
  description = "Triggers Step Function on AWS Config NON_COMPLIANT evaluation"

  event_pattern = jsonencode({
    source      = ["aws.config"]
    detail-type = ["Config Rules Compliance Change"]
    detail = {
      newEvaluationResult = {
        complianceType = ["NON_COMPLIANT"]
      }
    }
  })
}

resource "aws_cloudwatch_event_target" "step_function" {
  rule     = aws_cloudwatch_event_rule.non_compliant.name
  arn      = var.step_function_arn
  role_arn = aws_iam_role.eventbridge.arn
}

resource "aws_iam_role" "eventbridge" {
  name = "${var.environment}-eventbridge-sfn-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "events.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "start_execution" {
  name = "allow-start-execution"
  role = aws_iam_role.eventbridge.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "states:StartExecution"
      Resource = var.step_function_arn
    }]
  })
}
