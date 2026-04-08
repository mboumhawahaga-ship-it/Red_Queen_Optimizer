module "step_function" {
  source                = "../../modules/step-function"
  environment           = "dev"
  dry_run               = true
  max_budget            = 100
  grace_period_hours    = 24
  notify_lambda_arn     = module.cleanup_lambda.lambda_function_arn
  check_lambda_arn      = module.metrics_lambda.lambda_function_arn
  quarantine_lambda_arn = module.cleanup_lambda.lambda_function_arn
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
