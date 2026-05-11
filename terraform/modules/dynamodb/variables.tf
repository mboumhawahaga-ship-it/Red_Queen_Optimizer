variable "table_name" {
  description = "Nom de la table DynamoDB"
  type        = string
  default     = "redqueen-governance-state"
}

variable "environment" {
  description = "Environnement (dev, staging, prod)"
  type        = string
  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment doit être dev, staging ou prod"
  }
}

variable "enable_pitr" {
  description = "Activer le Point-In-Time Recovery (recommandé en prod)"
  type        = bool
  default     = false
}

variable "ttl_days" {
  description = "Durée de rétention des entrées en jours (via TTL)"
  type        = number
  default     = 90
}
