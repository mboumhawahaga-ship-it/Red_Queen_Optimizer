module "step_function" {
  source = "../../modules/step-function"

  environment        = "dev"
  dry_run            = true
  dynamodb_table_arn = module.governance_state.table_arn
  sns_topic_arn      = module.cleanup_lambda.sns_topic_arn
  feedback_url       = module.feedback.feedback_url
  feedback_secret    = var.feedback_secret
  slack_webhook_url  = var.slack_webhook_url
  log_retention_days = 7
}

module "eventbridge" {
  source            = "../../modules/eventbridge"
  environment       = "dev"
  step_function_arn = module.step_function.state_machine_arn
}

output "step_function_arn" {
  description = "ARN of the tagging governance Step Function"
  value       = module.step_function.state_machine_arn
}

output "eventbridge_rule_arn" {
  description = "ARN of the EventBridge rule watching Config NON_COMPLIANT events"
  value       = module.eventbridge.event_rule_arn
}
