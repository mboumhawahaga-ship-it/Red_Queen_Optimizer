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
  default     = ""
  description = "Fallback Owner tag value applied to untagged resources"
}

variable "default_squad" {
  type        = string
  default     = ""
  description = "Fallback Squad tag value applied to untagged resources"
}

variable "default_cost_center" {
  type        = string
  default     = ""
  description = "Fallback CostCenter tag value applied to untagged resources"
}

# ── Observability ─────────────────────────────────────────────────────────────

variable "log_retention_days" {
  type        = number
  default     = 14
  description = "CloudWatch log group retention in days"
}

# Empty lists deliberately grant no access to workload resources. ARN suffixes may
# contain wildcards for a reviewed prefix; region/account wildcards are rejected.
variable "governance_scope" {
  description = "Explicit workload ARN allow-lists. Empty means disabled for that service."
  type = object({
    ec2_instance_arns    = optional(list(string), [])
    s3_bucket_arns       = optional(list(string), [])
    rds_db_arns          = optional(list(string), [])
    lambda_function_arns = optional(list(string), [])
  })
  default = {}

  validation {
    condition = alltrue(concat(
      [for arn in var.governance_scope.ec2_instance_arns : can(regex("^arn:aws:ec2:[a-z0-9-]+:[0-9]{12}:instance/[a-zA-Z0-9*?_-]+$", arn))],
      [for arn in var.governance_scope.s3_bucket_arns : can(regex("^arn:aws:s3:::[a-z0-9][a-z0-9.*?-]*$", arn))],
      [for arn in var.governance_scope.rds_db_arns : can(regex("^arn:aws:rds:[a-z0-9-]+:[0-9]{12}:db:[a-zA-Z0-9*?_-]+$", arn))],
      [for arn in var.governance_scope.lambda_function_arns : can(regex("^arn:aws:lambda:[a-z0-9-]+:[0-9]{12}:function:[a-zA-Z0-9*?_-]+$", arn))],
    ))
    error_message = "Use workload ARNs of the specified type, with explicit AWS region and account. S3 uses bucket ARNs, without object paths or a global wildcard."
  }
}
