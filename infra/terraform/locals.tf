data "aws_caller_identity" "current" {}

locals {
  expected_aws_account_id = "107207236011"
  name                    = "${var.project_name}-${var.environment}"
  raw_bucket_prefix       = lower("${local.name}-${data.aws_caller_identity.current.account_id}-${var.aws_region}")
  # Reserve ten characters for the longest suffix, "-artifacts", so every
  # generated bucket name is lowercase, valid, and no more than 63 characters.
  bucket_prefix = trimsuffix(substr(local.raw_bucket_prefix, 0, 53), "-")

  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}
