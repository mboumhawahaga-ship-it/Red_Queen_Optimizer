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

variable "grace_period_hours" {
  description = "Période de grâce avant suppression (en heures)"
  type        = number
  default     = 24
}

variable "dry_run" {
  description = "Mode simulation (true) ou suppression réelle (false)"
  type        = bool
  default     = true
}

variable "notification_email" {
  description = "Email pour recevoir les notifications SNS"
  type        = string
  default     = ""
}

variable "enable_schedule" {
  description = "Activer l'exécution automatique planifiée"
  type        = bool
  default     = true
}

variable "schedule_expression" {
  description = "Expression cron pour la planification (ex: cron(0 2 * * ? *) = tous les jours à 2h)"
  type        = string
  default     = "cron(0 2 * * ? *)" # Tous les jours à 2h du matin
}

variable "log_retention_days" {
  description = "Durée de rétention des logs CloudWatch (en jours)"
  type        = number
  default     = 7
}

variable "dynamodb_table_arn" {
  description = "ARN de la table DynamoDB redqueen-governance-state"
  type        = string
}

variable "feedback_url" {
  description = "URL publique de la Lambda feedback (pour les liens dans les emails SNS)"
  type        = string
  default     = ""
}

variable "feedback_secret" {
  description = "Secret HMAC pour signer les tokens des liens de feedback"
  type        = string
  sensitive   = true
  default     = ""
}
