variable "aws_region" {
  description = "AWS Region in which to create the POC foundation."
  type        = string

  validation {
    condition     = var.aws_region == "us-east-1"
    error_message = "Milestone 4 Bedrock deployment requires aws_region=us-east-1."
  }
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

  validation {
    condition     = length(var.availability_zones) == 2 && length(toset(var.availability_zones)) == 2
    error_message = "availability_zones must contain exactly two distinct availability zones."
  }
}

variable "public_subnet_cidrs" {
  description = "Two public subnet CIDR blocks, one for each availability zone."
  type        = list(string)

  validation {
    condition     = length(var.public_subnet_cidrs) == 2 && alltrue([for cidr in var.public_subnet_cidrs : can(cidrnetmask(cidr))])
    error_message = "public_subnet_cidrs must contain exactly two valid CIDR blocks ordered to match availability_zones."
  }
}

variable "private_subnet_cidrs" {
  description = "Two private ECS-task subnet CIDR blocks, one for each availability zone."
  type        = list(string)

  validation {
    condition     = length(var.private_subnet_cidrs) == 2 && alltrue([for cidr in var.private_subnet_cidrs : can(cidrnetmask(cidr))])
    error_message = "private_subnet_cidrs must contain exactly two valid CIDR blocks ordered to match availability_zones."
  }
}

variable "bedrock_model_arns" {
  description = "Bedrock foundation-model ARNs that the ai-app task may invoke. Defaults to the Milestone 4 model only."
  type        = list(string)
  default = [
    "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-micro-v1:0",
  ]

  validation {
    condition     = length(var.bedrock_model_arns) == 1 && var.bedrock_model_arns[0] == "arn:aws:bedrock:us-east-1::foundation-model/amazon.nova-micro-v1:0"
    error_message = "Milestone 4 permits only the us-east-1 amazon.nova-micro-v1:0 foundation-model ARN."
  }
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

variable "enable_budget_alerts" {
  description = "Whether to create the AWS monthly budget and spend alerts."
  type        = bool
  default     = false
}

variable "budget_alert_email" {
  description = "Email address for AWS Budget alerts. Used when enable_budget_alerts is true. Provide via ignored local terraform.tfvars or TF_VAR_budget_alert_email."
  type        = string
  default     = null

  validation {
    condition     = var.budget_alert_email == null || var.budget_alert_email == "" || can(regex("^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$", var.budget_alert_email))
    error_message = "budget_alert_email must be a valid email address when provided."
  }
}

variable "monthly_budget_limit_usd" {
  description = "Monthly budget limit in USD for the POC account cost alert."
  type        = string
  default     = "10.0"

  validation {
    condition     = can(regex("^[0-9]+(\\.[0-9]{1,2})?$", var.monthly_budget_limit_usd)) && can(tonumber(var.monthly_budget_limit_usd)) && tonumber(var.monthly_budget_limit_usd) > 0
    error_message = "monthly_budget_limit_usd must be a positive numeric dollar amount (e.g. 10.0 or 10)."
  }
}
