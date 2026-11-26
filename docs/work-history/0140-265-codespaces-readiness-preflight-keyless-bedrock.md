# 0140 — Codespaces readiness, unified preflight, and protected keyless Bedrock eval smoke (#265)

## Goal
Implement issue #265 by adding:
- a reproducible Codespaces devcontainer baseline,
- unified preflight entrypoints for env/services/eval readiness,
- a protected manual OIDC workflow for bounded Bedrock eval smoke,
- and documentation for SSO/device login, security boundaries, budget controls, teardown, and owner-only setup blockers.

## Starting point
- No `.devcontainer/` configuration existed.
- Existing checks were spread across many Make/eval/smoke commands with no single preflight entrypoint.
- OIDC in this repo existed for Terraform release, but no dedicated protected Bedrock eval smoke workflow.
- No dedicated runbook documented Codespaces + keyless Bedrock setup as one path.

## Decisions
- **Devcontainer baseline:** Added `.devcontainer/devcontainer.json` + `.devcontainer/post-create.sh` with Python 3.12, Node 24, Docker-outside-of-Docker, AWS CLI, host requirements (`4 CPU / 16 GB / 32 GB`), and bootstrap commands (`make check-bootstrap`, `make preflight-env`).
- **Unified preflight surface:** Added `scripts/preflight/readiness.py` and Make targets:
  - `make preflight-env`
  - `make preflight-services`
  - `make preflight-eval`
  - `make preflight-runtime-local`
  - `make preflight-runtime-aws`
  - `make preflight`
  - `make preflight-paid-smoke` (explicit `EVAL_CONFIRM=1` gate)
  Checks cover required tooling, temporary-credential validation (allow STS/OIDC session sets, reject permanent/incomplete env keys), dataset split/holdout contract, LiteLLM alias and Bedrock config markers, budget parsing, MCP/Redis/SSE and observability topology markers, eval baseline/holdout assets, and runtime stack readiness using existing smoke/health commands.
- **Protected keyless CI path:** Added `.github/workflows/bedrock-eval-smoke.yml` with:
  - `workflow_dispatch` only,
  - protected environment `bedrock-eval-protected`,
  - minimal permissions (`contents: read`, `id-token: write`),
  - SHA-pinned third-party actions,
  - short credential lifetime (`1800s`),
  - trusted ref guard (**dispatch context `refs/heads/main` + checkout pinned to `refs/heads/main` + post-checkout provenance check against `origin/main` before OIDC credential exchange**),
  - bounded smoke flow (`eval-start` + `eval-smoke` + artifact upload + teardown),
  - sanitized artifact upload only (`artifacts/bedrock-eval-smoke/summary.json`; no raw eval captures/logs).
- **Runbook:** Added `docs/codespaces-preflight-bedrock.md` for Codespaces sizing/idle/budget guidance, SSO/device login, protected OIDC setup, trust policy template, least-privilege intent, cost controls, and teardown.
- **Discoverability:** Added README link under “Developer platform readiness”.

## Verification
- `python3 -m pytest tests/scripts/test_codespaces_preflight.py tests/scripts/test_bedrock_eval_workflow.py -q` -> `7 passed`
- `make preflight-env` -> `preflight env: ok`
- `make preflight-services` -> `preflight services: ok`
- `make preflight-eval` -> `preflight eval: ok`
- `make preflight-runtime-local` -> **NOT RUN** (requires active local stack)
- `make preflight-runtime-aws` -> **NOT RUN** (requires active real-AWS stack + approved credentials)

## Limitations / blockers
- Live GitHub Bedrock smoke execution requires owner/admin setup that cannot be completed from repository code:
  - IAM OIDC role creation/update,
  - protected environment configuration and approvals,
  - repository/environment variables (`BEDROCK_EVAL_ROLE_ARN`, `AWS_REGION`, `DYNAMODB_TABLE_NAME`, budget vars).
- Without that setup, workflow behavior is statically validated by tests but not executed live in this task.
