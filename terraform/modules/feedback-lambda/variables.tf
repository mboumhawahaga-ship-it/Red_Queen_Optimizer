variable "environment" {
  description = "Environnement (dev, staging, prod)"
  type        = string
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment doit être dev, staging ou prod"
  }
}

variable "aws_region" {
  description = "Région AWS de déploiement"
  type        = string
  default     = "eu-west-1"
}

variable "dry_run" {
  description = "true = simulation, false = tags appliqués réellement"
  type        = bool
  default     = true
}

variable "dynamodb_table_arn" {
  description = "ARN de la table DynamoDB redqueen-governance-state"
  type        = string
}

variable "feedback_secret" {
  description = "Secret HMAC pour signer les tokens des liens de feedback"
  type        = string
  sensitive   = true
}

variable "log_retention_days" {
  description = "Durée de rétention des logs CloudWatch (en jours)"
  type        = number
  default     = 7
}
