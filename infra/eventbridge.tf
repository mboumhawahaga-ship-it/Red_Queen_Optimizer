# CRITICAL ROUTING RULES — do not collapse these into a single rule:
#
#   CloudTrail creates  → auto-tagger ONLY        (applies default tags on new resources)
#   Config NON_COMPLIANT → compliance-evaluator ONLY  (evaluates SLA + quarantine)
#
# Merging them would cause a race condition where compliance-evaluator fires before
# auto-tagger has finished tagging the resource.

# ── CloudTrail resource creation → auto-tagger ───────────────────────────────

resource "aws_cloudwatch_event_rule" "cloudtrail_creates" {
  name        = "${local.name_prefix}-cloudtrail-creates"
  description = "Routes CloudTrail resource-creation events to auto-tagger only"

  event_pattern = jsonencode({
    source        = ["aws.cloudtrail"]
    "detail-type" = ["AWS API Call via CloudTrail"]
    detail = {
      eventName = [
        "RunInstances",
        "CreateBucket",
        "CreateDBInstance",
        "CreateFunction20150331",
      ]
    }
  })

  tags = { Name = "${local.name_prefix}-cloudtrail-creates" }
}

resource "aws_cloudwatch_event_target" "auto_tagger" {
  rule      = aws_cloudwatch_event_rule.cloudtrail_creates.name
  target_id = "AutoTaggerTarget"
  arn       = aws_lambda_function.auto_tagger.arn
}

resource "aws_lambda_permission" "auto_tagger_eventbridge" {
  statement_id  = "AllowEventBridgeCloudTrailInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.auto_tagger.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.cloudtrail_creates.arn
}

# ── Config NON_COMPLIANT → compliance-evaluator ───────────────────────────────

resource "aws_cloudwatch_event_rule" "config_non_compliant" {
  name        = "${local.name_prefix}-config-non-compliant"
  description = "Routes AWS Config NON_COMPLIANT findings to compliance-evaluator only"

  event_pattern = jsonencode({
    source        = ["aws.config"]
    "detail-type" = ["Config Rules Compliance Change"]
    detail = {
      newEvaluationResult = {
        complianceType = ["NON_COMPLIANT"]
      }
    }
  })

  tags = { Name = "${local.name_prefix}-config-non-compliant" }
}

resource "aws_cloudwatch_event_target" "compliance_evaluator" {
  rule      = aws_cloudwatch_event_rule.config_non_compliant.name
  target_id = "ComplianceEvaluatorTarget"
  arn       = aws_lambda_function.compliance_evaluator.arn
}

resource "aws_lambda_permission" "compliance_evaluator_eventbridge" {
  statement_id  = "AllowEventBridgeConfigInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.compliance_evaluator.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.config_non_compliant.arn
}

# ── EventBridge Scheduler → compliance-evaluator (SLA re-invoke) ─────────────
# Schedules are created dynamically by compliance-evaluator at alert time;
# the permission covers all schedules in the default group.

resource "aws_lambda_permission" "compliance_evaluator_scheduler" {
  statement_id  = "AllowEventBridgeSchedulerInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.compliance_evaluator.function_name
  principal     = "scheduler.amazonaws.com"
  source_arn    = "arn:aws:scheduler:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:schedule/default/*"
}
