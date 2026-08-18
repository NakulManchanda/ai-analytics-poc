data "aws_caller_identity" "current" {}

locals {
  name          = "${var.project_name}-${var.environment}"
  bucket_prefix = "${local.name}-${data.aws_caller_identity.current.account_id}-${var.aws_region}"

  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }
}
