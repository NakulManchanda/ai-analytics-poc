# 0034 — Authorize bedrock:InvokeModelWithResponseStream in IAM Task Policy

## Goal
Authorize `bedrock:InvokeModelWithResponseStream` in the `ai-app` ECS task IAM role policy (`infra/terraform/iam.tf`) so that Bedrock streaming calls (`converse_stream`) succeed in deployed AWS without encountering `AccessDeniedException`.

## Starting Point
PR #62 added genuine Bedrock token streaming (`converse_stream`). However, `infra/terraform/iam.tf` only authorized `bedrock:InvokeModel`, causing live AWS calls on ECS to fail with `AccessDeniedException`.

## Decisions
- Updated `infra/terraform/iam.tf` `InvokeConfiguredBedrockModels` statement `actions` to `["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]`.
- Added unit test in `tests/infra/test_iam_bedrock.py` and updated static assertion in `tests/infra/test_budget.py`.

## Verification
- `uv run --project services/app pytest tests` passed all 19 infra tests.
- `make test` passing.
