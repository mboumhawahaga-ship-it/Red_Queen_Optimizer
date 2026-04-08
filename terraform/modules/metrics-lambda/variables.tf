variable "environment" {
  description = "Environnement (dev, staging, prod)"
  type        = string
  default     = "dev"
}

variable "aws_region" {
  description = "Région AWS de déploiement"
  type        = string
  default     = "eu-west-1"
}

variable "enable_schedule" {
  description = "Activer l'execution automatique planifiee"
  type        = bool
  default     = true
}

variable "schedule_expression" {
  description = "Expression pour la planification (ex: rate(6 hours))"
  type        = string
  default     = "rate(6 hours)"

  validation {
    condition = can(regex(
      "^(rate\\(\\d+ (minute|minutes|hour|hours|day|days)\\)|cron\\(.+\\))$",
      var.schedule_expression
    ))
    error_message = "Format invalide. Exemples : rate(6 hours) ou cron(0 */6 * * ? *)"
  }
}

variable "log_retention_days" {
  description = "Duree de retention des logs CloudWatch (en jours)"
  type        = number
  default     = 7
}
