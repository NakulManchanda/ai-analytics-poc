# Codespaces readiness, preflight, and keyless Bedrock eval access

This runbook documents Issue #265 and is scoped to **repeatable development/evaluation setup**, not production deployment.

## 1) Codespaces baseline

The repository now includes `.devcontainer/devcontainer.json` with:
- Python 3.12 base image
- Node 24, Docker-outside-of-Docker, AWS CLI features
- host requirements (`4 vCPU`, `16 GB RAM`, `32 GB storage`)
- `postCreateCommand` (`.devcontainer/post-create.sh`) that installs `jq`, installs `uv` when missing, and runs:
  - `make check-bootstrap`
  - `make preflight-env`

### Recommended Codespaces settings (manual)

These are account-level/operator choices and must be applied in GitHub UI:
- **Machine type**: 4-core or higher (smaller machines can be too slow for local Docker build/test cycles).
- **Storage**: keep at least 32 GB free for Docker layers + eval artifacts.
- **Idle timeout / stop policy**: use short timeout for cost control (for example 30 minutes idle stop).
- **Usage budget alerts**: set a Codespaces spending limit/alert in GitHub billing settings.

## 2) Unified preflight commands

Run from repository root:

```bash
make preflight-env
make preflight-services
make preflight-eval
make preflight
```

What they validate:
- **Environment/tooling**: required CLI tools, no static AWS env keys, dataset split contract, LiteLLM alias config, budget/feature-flag parsing.
- **Services**: MCP/Redis/SSE topology markers, observability overlay presence, required smoke scripts.
- **Eval readiness**: baseline/holdout artifacts, split/test assets, docs markers for `EVAL_CONFIRM`, baseline, holdout.

Paid inference remains explicit:

```bash
make preflight-paid-smoke EVAL_CONFIRM=1
```

`preflight-paid-smoke` intentionally refuses to run without `EVAL_CONFIRM=1`.

## 3) Interactive AWS access in Codespaces (no long-lived keys)

Use AWS IAM Identity Center SSO/device login inside the Codespace terminal:

```bash
aws configure sso
aws sso login --profile <your-profile>
AWS_PROFILE=<your-profile> aws sts get-caller-identity
```

Guidelines:
- Do **not** export/store permanent `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` in repo, `.env`, or GitHub secrets.
- Refresh temporary credentials with `aws sso login` when needed.
- Use read-only bind mounts (`~/.aws:/aws:ro`) as configured in `docker-compose.aws.yml`.

## 4) Protected keyless GitHub Actions eval workflow

Workflow: `.github/workflows/bedrock-eval-smoke.yml`

Security shape:
- `workflow_dispatch` only (no `pull_request` / `pull_request_target` triggers)
- protected environment: `bedrock-eval-protected`
- least permissions: `contents: read`, `id-token: write`
- short-lived credentials (`role-duration-seconds: 1800`)
- third-party actions pinned to reviewed commit SHAs
- trusted refs only (`main` or signed `v*` tag pattern)
- bounded single smoke flow: preflight -> stack up -> `eval-smoke` -> artifact capture -> teardown

### Manual owner-only setup required before enabling live runs

These steps require AWS/GitHub admin access and are not executable from repository code:

1. **Create eval IAM role** (example name: `ai-analytics-bedrock-eval-gha`) with OIDC trust restricted to this repo + protected environment + workflow.
2. **Attach least-privilege policy** allowing only required Bedrock models/inference profile and minimal S3 artifact paths.
3. **Create protected GitHub environment** `bedrock-eval-protected`:
   - required reviewers
   - deployment-branch restrictions (for example `main` and approved `v*` tags)
   - environment variables: `AWS_REGION`, `DYNAMODB_TABLE_NAME`, `GLOBAL_BEDROCK_MONTHLY_LIMIT_USD`, `BEDROCK_EVAL_ROLE_ARN`
4. **Do not add static AWS credentials** to repository or org secrets.

### Example trust-policy constraints

Use as a template and adapt account IDs/role names:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Federated": "arn:aws:iam::<account-id>:oidc-provider/token.actions.githubusercontent.com"
      },
      "Action": "sts:AssumeRoleWithWebIdentity",
      "Condition": {
        "StringEquals": {
          "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
          "token.actions.githubusercontent.com:sub": "repo:NakulManchanda/ai-analytics-poc:environment:bedrock-eval-protected",
          "token.actions.githubusercontent.com:job_workflow_ref": "NakulManchanda/ai-analytics-poc/.github/workflows/bedrock-eval-smoke.yml@refs/heads/main"
        }
      }
    }
  ]
}
```

## 5) Cost controls and quotas

This repository uses layered controls; understand which are hard-enforced vs alert-only:
- **Hard app-level controls**: execution budgets + shared Bedrock monthly limit (`GLOBAL_BEDROCK_MONTHLY_LIMIT_USD`).
- **Workflow controls**: manual dispatch + protected approval + bounded smoke scope + timeout + concurrency.
- **Account alerts**: AWS Budgets notifications are alerts, not hard stop.

## 6) Teardown and evidence

After manual/local runs:

```bash
docker compose -f docker-compose.yml -f docker-compose.aws.yml -f docker-compose.litellm.yml -f docker-compose.phoenix.yml -f docker-compose.phoenix-litellm.yml down --remove-orphans
```

For workflow runs, artifacts are captured from `metrics/evals/<run_id>/...` and uploaded as CI artifacts.

## 7) Verified blocker list

- IAM role creation/update, OIDC trust wiring, and protected environment approval rules require account-owner/repository-admin action.
- Until those are configured, `bedrock-eval-smoke.yml` can be reviewed and statically tested but cannot perform live Bedrock smoke in CI.
