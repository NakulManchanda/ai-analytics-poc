variable "aws_region" {
  type        = string
  description = "AWS Region for the Terraform state bucket."
}

variable "state_bucket_name" {
  type        = string
  description = "Globally unique private S3 bucket name for Terraform state."

  validation {
    condition     = can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.state_bucket_name))
    error_message = "state_bucket_name must be a valid S3 bucket name."
  }
}
