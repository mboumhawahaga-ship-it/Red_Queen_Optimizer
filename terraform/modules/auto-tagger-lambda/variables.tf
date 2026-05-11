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
  description = "true = simulation uniquement, false = tags appliqués réellement"
  type        = bool
  default     = true
}

variable "dynamodb_table_arn" {
  description = "ARN de la table DynamoDB redqueen-governance-state"
  type        = string
}

variable "default_squad" {
  description = "Valeur par défaut du tag Squad si absent de la requête"
  type        = string
  default     = "unknown"
}

variable "default_cost_center" {
  description = "Valeur par défaut du tag CostCenter si absent de la requête"
  type        = string
  default     = "CC-000"
}

variable "default_owner" {
  description = "Valeur par défaut du tag Owner si non déductible de l'identité IAM"
  type        = string
  default     = "auto-tagger@entreprise.com"
}

variable "log_retention_days" {
  description = "Durée de rétention des logs CloudWatch (en jours)"
  type        = number
  default     = 7
}
