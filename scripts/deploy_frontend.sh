#!/usr/bin/env bash
set -euo pipefail

# Milestone 15: Build and Deploy Frontend to S3 + Invalidate CloudFront Cache

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

frontend_bucket="${FRONTEND_BUCKET:-}"
distribution_id="${CLOUDFRONT_DISTRIBUTION_ID:-}"

if [[ -z "${frontend_bucket}" || -z "${distribution_id}" ]]; then
  if command -v terraform >/dev/null 2>&1 && [[ -d "${REPO_ROOT}/infra/terraform/.terraform" ]]; then
    frontend_bucket="$(terraform -chdir="${REPO_ROOT}/infra/terraform" output -raw frontend_bucket_name 2>/dev/null || true)"
    distribution_id="$(terraform -chdir="${REPO_ROOT}/infra/terraform" output -raw cloudfront_distribution_id 2>/dev/null || true)"
  fi
fi

if [[ -z "${frontend_bucket}" ]]; then
  echo "Error: FRONTEND_BUCKET is required (or apply Terraform foundation first)." >&2
  exit 1
fi

echo "==> [M15] Building React Web UI production bundle..."
npm --prefix "${REPO_ROOT}/web" run build

echo "==> [M15] Syncing dist/ to private S3 bucket s3://${frontend_bucket}..."
aws s3 sync "${REPO_ROOT}/web/dist" "s3://${frontend_bucket}" --delete

if [[ -n "${distribution_id}" ]]; then
  echo "==> [M15] Creating CloudFront cache invalidation for distribution ${distribution_id}..."
  aws cloudfront create-invalidation --distribution-id "${distribution_id}" --paths "/*"
fi

echo "==> [M15] Frontend build and sync complete!"
