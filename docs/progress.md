# Current milestone

Milestone 15 — S3 + CloudFront public frontend deployment

## Status

IN PROGRESS — [issue #30](https://github.com/NakulManchanda/ai-analytics-poc/issues/30)

## Merged milestone baseline

- **Milestones 0–14**: Merged (`bb4b27e`), including ECS/Fargate backend services, ALB, local multi-service integration hardening, async job worker, Redis Streams SSE, and bounded-context visualization.
- **Milestone 13 Foundation**: Terraform infrastructure foundation and budget alerts merged.

## Acceptance criteria

- [x] CloudFront distribution with Origin Access Control (OAC) for private S3 frontend bucket.
- [x] S3 bucket policy allowing `s3:GetObject` strictly for CloudFront distribution ARN.
- [x] Default cache behavior (`/*`) serving static React SPA with HTTPS redirection and `CachingOptimized`.
- [x] Ordered cache behavior (`/api/*`) proxying dynamic requests to ALB with `CachingDisabled` and `AllViewerExceptHostHeader`.
- [x] Custom error responses (403/404 -> `/index.html`) configured for SPA client-side routing.
- [x] `scripts/deploy_frontend.sh` automated build/sync/invalidation script added.
- [x] `scripts/smoke/15_cloudfront_e2e.sh` and `make cloudfront-smoke` added.
- [x] Full test suite passing across all services (`make test`).

## Next milestone

Milestone 16 (final demo, security review, and documentation, #31) gates on Milestone 15 merging.
