variable "feedback_secret" {
  description = "Secret HMAC pour signer les tokens des liens de feedback (générez avec : openssl rand -hex 32)"
  type        = string
  sensitive   = true
}

variable "slack_webhook_url" {
  description = "URL du webhook Slack pour les alertes (optionnel)"
  type        = string
  sensitive   = true
  default     = ""
}
