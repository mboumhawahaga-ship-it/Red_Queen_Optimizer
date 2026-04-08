variable "environment" {
  description = "Deployment environment (dev, staging, prod)"
  type        = string
}

variable "step_function_arn" {
  description = "ARN of the Step Function to trigger on NON_COMPLIANT events"
  type        = string
}
