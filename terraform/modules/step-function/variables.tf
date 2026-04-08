variable "environment" {
  description = "Nom de l'environnement (dev, staging, prod)"
  type        = string
}

variable "dry_run" {
  description = "Mode dry-run : aucune action réelle si true"
  type        = bool
  default     = true
}

variable "grace_period_hours" {
  description = "Durée de la période de grâce avant quarantaine (en heures)"
  type        = number
  default     = 24
}

variable "notify_lambda_arn" {
  description = "ARN de la Lambda de notification du propriétaire (SNS)"
  type        = string
}

variable "check_lambda_arn" {
  description = "ARN de la Lambda de vérification de conformité des tags"
  type        = string
}

variable "quarantine_lambda_arn" {
  description = "ARN de la Lambda de quarantaine (stop instance uniquement, jamais suppression)"
  type        = string
}
