variable "aws_region" {
  description = "AWS Region in which to create the POC foundation."
  type        = string
}

variable "project_name" {
  description = "Short, lowercase project identifier used in resource names."
  type        = string
  default     = "ai-analytics-poc"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,23}$", var.project_name))
    error_message = "project_name must be 3-24 lowercase letters, digits, or hyphens and begin with a letter."
  }
}

variable "environment" {
  description = "Deployment environment name used in resource names and tags."
  type        = string
  default     = "demo"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,11}$", var.environment))
    error_message = "environment must be 2-12 lowercase letters, digits, or hyphens and begin with a letter."
  }
}

variable "vpc_cidr" {
  description = "CIDR block for the POC VPC."
  type        = string
  default     = "10.42.0.0/16"
}

variable "availability_zones" {
  description = "Exactly two availability zones in aws_region, ordered to match the subnet CIDR lists."
  type        = list(string)
}

variable "public_subnet_cidrs" {
  description = "Two public subnet CIDR blocks, one for each availability zone."
  type        = list(string)
}

variable "private_subnet_cidrs" {
  description = "Two private ECS-task subnet CIDR blocks, one for each availability zone."
  type        = list(string)
}

variable "bedrock_model_arns" {
  description = "Bedrock foundation-model ARNs that the ai-app task may invoke. Leave empty until a model is selected."
  type        = list(string)
  default     = []
}

variable "log_retention_in_days" {
  description = "CloudWatch log retention period for ECS service logs."
  type        = number
  default     = 7

  validation {
    condition     = contains([1, 3, 5, 7, 14, 30, 60, 90], var.log_retention_in_days)
    error_message = "log_retention_in_days must be a supported short POC retention period."
  }
}
