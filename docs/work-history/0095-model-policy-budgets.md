# Work 0095 - Model-role policy, token accounting, budgets, cost

## Milestone

Epic #167, issue #170 (2 of 4)

## Branch / Worktree

`feat/170-model-policy`, `.worktrees/170-model-policy`

## Goal

Per-role model policy with entitlements, per-call and per-run limits, versioned pricing, and estimated-vs-billed cost on every result.

## Starting state

`LiteLLMClient` chose planner/answer aliases inline; only the DynamoDB monthly guard limited spend; missing usage became 0.

## Decisions

- New `app/model_policy/` (policy, budget, pricing, call context); `LiteLLMClient._governed` wraps each call.
- DynamoDB monthly guard stays authoritative; run budgets are narrower in-process limits; proxy budgets are documentation-only.
- `LLMResult`/`ToolProposalResult` gained optional fields (role, model_alias, pricing_version, cost_usd, cost_kind, usage_known, fallback_used), merged as small first commits for capture (#169).
- Run identity arrives via a contextvar because `orchestration/loop.py` is owned by another track.
- Fallbacks: same-provider and tenant-entitled only; transient errors only; never past the monthly guard.

## Verification

`make test`: app 248 passed, bench 133, gateway 396, MCP 20, dataset 48, infra 93 (1 skipped); `web-test` not run (no `npm` in this environment). Black and ruff clean.

## Lessons / limitations

Pending integration: per-run budgets and tenant entitlements are NOT operational until the integration PR binds `call_context(tenant_id, run_id)` in `orchestration/loop.py`. Until then tenant is `default` and no run budget applies; only the DynamoDB monthly guard is enforced. With `RUN_MAX_USD` set, a call whose cost cannot be estimated is denied (fail closed).

Run budgets need `call_context(...)` from the loop to take effect. Proxy-side budgets and key issuance are not implemented.
