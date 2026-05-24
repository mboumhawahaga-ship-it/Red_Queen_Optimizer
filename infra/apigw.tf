# HTTP API (API Gateway v2) — simpler and cheaper than REST API (v1).
#
# payload_format_version = "1.0" is required because feedback-api/handler.py reads
# event["httpMethod"] and event["queryStringParameters"], which are the v1 payload format.
# v2 format uses event["requestContext"]["http"]["method"] instead.

resource "aws_apigatewayv2_api" "feedback" {
  name          = "${local.name_prefix}-feedback"
  protocol_type = "HTTP"
  description   = "Red Queen governance feedback API — GET and POST /feedback"

  cors_configuration {
    allow_methods = ["GET", "POST", "OPTIONS"]
    allow_origins = ["*"]
    allow_headers = ["Content-Type", "Authorization"]
    max_age       = 3600
  }

  tags = { Name = "${local.name_prefix}-feedback" }
}

resource "aws_apigatewayv2_integration" "feedback_lambda" {
  api_id                 = aws_apigatewayv2_api.feedback.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.feedback_api.invoke_arn
  payload_format_version = "1.0"
}

resource "aws_apigatewayv2_route" "get_feedback" {
  api_id    = aws_apigatewayv2_api.feedback.id
  route_key = "GET /feedback"
  target    = "integrations/${aws_apigatewayv2_integration.feedback_lambda.id}"
}

resource "aws_apigatewayv2_route" "post_feedback" {
  api_id    = aws_apigatewayv2_api.feedback.id
  route_key = "POST /feedback"
  target    = "integrations/${aws_apigatewayv2_integration.feedback_lambda.id}"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.feedback.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.apigw.arn
    format = jsonencode({
      requestId        = "$context.requestId"
      ip               = "$context.identity.sourceIp"
      httpMethod       = "$context.httpMethod"
      routeKey         = "$context.routeKey"
      status           = "$context.status"
      responseLength   = "$context.responseLength"
      integrationError = "$context.integrationErrorMessage"
    })
  }

  tags = { Name = "${local.name_prefix}-feedback-default" }
}

resource "aws_lambda_permission" "feedback_api_apigw" {
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.feedback_api.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.feedback.execution_arn}/*/*/feedback"
}
