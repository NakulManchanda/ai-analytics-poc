# Work 0023 — Milestone 15 S3 + CloudFront public frontend deployment

## Goal

Deploy the public production entry point: React SPA in private S3 bucket served through Amazon CloudFront with Origin Access Control (OAC), routing `/*` to S3 and `/api/*` to the Application Load Balancer with SSE streaming support and HTTPS.

## Starting state

`origin/main` at `bb4b27e` has Milestones 0–14 merged (including ECS/Fargate backend services and ALB deployment).

## Decisions

- **CloudFront Distribution (`infra/terraform/cloudfront.tf`)**:
  - Configured `aws_cloudfront_origin_access_control.frontend` for secure, private S3 frontend bucket access.
  - Added S3 bucket policy granting read-only access strictly to the CloudFront distribution ARN.
  - Configured default cache behavior (`/*`) serving static assets from S3 with HTTPS redirection and `CachingOptimized`.
  - Configured ordered cache behavior (`/api/*`) proxying dynamic API requests to the ALB origin with `CachingDisabled` and `AllViewerExceptHostHeader`.
  - Added custom error responses for 403 and 404 rewriting to `/index.html` (HTTP 200) to support SPA client-side routing.
- **Frontend Build & Deploy Tooling (`scripts/deploy_frontend.sh`)**:
  - Created automated script to build React production bundle (`npm run build`), synchronize `web/dist/` to the private S3 frontend bucket, and create a CloudFront invalidation for `/*`.
- **E2E Smoke Verification (`scripts/smoke/15_cloudfront_e2e.sh`)**:
  - Created end-to-end verification script testing CloudFront root page load, `/api/health` proxying, and `/api/ask` query execution over HTTPS.
- **Outputs & Make Targets**:
  - Added `cloudfront_domain_name`, `cloudfront_distribution_id`, `cloudfront_url`, and console links in `infra/terraform/outputs.tf`.
  - Added `cloudfront-smoke` and `deploy-frontend` targets to root `Makefile`.
  - Added unit test suite `tests/infra/test_cloudfront.py`.

## Verification

- `terraform fmt -check` and `terraform validate` passed.
- `uv run --project services/app ruff check services/app tests` and `black --check` passed cleanly with 0 errors.
- Full test suite `make test` passed across all services (108 backend tests + 10 React vitest tests).

## Pull request and merge state

Branch `feat/m15-cloudfront-frontend` tracks [issue #30](https://github.com/NakulManchanda/ai-analytics-poc/issues/30).
