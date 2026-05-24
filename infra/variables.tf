variable "aws_region" {
  type        = string
  default     = "eu-west-1"
  description = "AWS region for all resources"
}

variable "environment" {
  type        = string
  default     = "dev"
  description = "Deployment environment (dev | staging | prod)"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod"
  }
}

variable "project" {
  type        = string
  default     = "red-queen"
  description = "Project name prefix applied to every resource name"
}

variable "dry_run" {
  type        = bool
  default     = true
  description = "When true, Lambdas log intent but skip all writes to AWS APIs"
}

variable "sns_email" {
  type        = string
  description = "Email address that receives governance alert notifications"
}

variable "slack_webhook_url" {
  type        = string
  sensitive   = true
  default     = ""
  description = "Slack incoming webhook URL (leave empty to disable Slack notifications)"
}

# ── Auto-tagger defaults ──────────────────────────────────────────────────────

variable "default_owner" {
  type        = string
  default     = "auto-tagger@entreprise.com"
  description = "Fallback Owner tag value applied to untagged resources"
}

variable "default_squad" {
  type        = string
  default     = "unknown"
  description = "Fallback Squad tag value applied to untagged resources"
}

variable "default_cost_center" {
  type        = string
  default     = "CC-000"
  description = "Fallback CostCenter tag value applied to untagged resources"
}

# ── Observability ─────────────────────────────────────────────────────────────

variable "log_retention_days" {
  type        = number
  default     = 14
  description = "CloudWatch log group retention in days"
}
