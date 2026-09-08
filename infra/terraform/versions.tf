terraform {
  # Native S3 lockfiles require Terraform 1.10+. Keep the lower bound at the
  # version exercised by local and GitHub Actions validation.
  required_version = ">= 1.16.1, < 2.0.0"

  # Values which identify an account are intentionally supplied through an
  # ignored backend.hcl locally or protected GitHub environment variables.
  backend "s3" {
    use_lockfile = true
  }

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}
