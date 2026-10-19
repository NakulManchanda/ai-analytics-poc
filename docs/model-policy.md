# Model-role policy and spend guards (operator guide)

Issue #170, epic #167. Code: `services/app/app/model_policy/`, wired into `LiteLLMClient` (`LLM_PROVIDER=litellm`).

## Roles and aliases

The app picks a role (`classifier`, `planner`, `answer`); policy maps it to a proxy alias. Today every alias targets
the same Bedrock Nova Micro model. Mapping is static config; there is no LLM-based routing.

Swap models without code: write a JSON policy and set `MODEL_POLICY_FILE=/path/policy.json`.

```json
{
  "providers": {"taxi-planner": "bedrock", "taxi-answer": "bedrock", "taxi-answer-large": "bedrock"},
  "roles": {
    "planner": {"alias": "taxi-planner", "max_tokens": 512},
    "answer": {"alias": "taxi-answer-large", "max_tokens": 1024, "fallbacks": ["taxi-answer"]}
  },
  "tenant_allowlists": {"*": ["taxi-planner", "taxi-answer"], "acme": ["taxi-planner", "taxi-answer", "taxi-answer-large"]}
}
```

Every alias must exist in `infra/litellm/config.yaml`. Without the file, `LITELLM_PLANNER_ALIAS` / `LITELLM_ANSWER_ALIAS` apply.

## Controls

| Control | Where | Failure code (non-retryable) |
| --- | --- | --- |
| Tenant entitlement (`tenant_allowlists`; omitted = all) | app policy | `model_not_entitled` |
| Per-call `max_tokens` per role | app policy | clamped, not an error |
| Per-run budget (`RUN_MAX_TOKENS`, `RUN_MAX_USD`; with a USD cap, an unpriced model is denied, fail closed) | app, in-process, atomic reserve/commit | `run_budget_exhausted` |
| Monthly Bedrock cap (`GLOBAL_BEDROCK_MONTHLY_LIMIT_USD`) | DynamoDB `app.bedrock_budget` | `bedrock_budget_exhausted` |
| Fallbacks | policy allowlist, same provider only | original error is raised |

Authoritative layer: the DynamoDB monthly guard remains the hard cap (it is durable and shared across tasks). Per-run
budgets are in-process and reset on restart; they bound a single runaway run, not monthly spend. Proxy budgets are
defense in depth only and are not relied on until verified. A policy fallback that crosses providers is refused at load
(`PolicyConfigError`) unless `allow_cross_provider_fallback` is set explicitly; there is no silent cross-provider egress.
A fallback the tenant is not entitled to is skipped and logged (`fallback_rejected`); the original error surfaces.

**Binding:** `OrchestrationLoop.execute()` wraps each run in `call_context(DEFAULT_TENANT, run_id)` (#176), so per-run budgets and tenant entitlements apply to every model call of every normal run, including calls made in cancellation worker threads (the context is copied). The tenant is still the single `default` tenant; real tenant identity arrives with auth. A budget refusal surfaces as `OrchestrationError` code `run_budget_exhausted` (the run is persisted as `failed` and re-raised to the API layer).

## Cost accounting

Each `LLMResult` carries `role`, `model_alias`, `pricing_version`, `cost_usd`, `cost_kind`, `usage_known`, `fallback_used`.
`cost_kind` is `billed` when the proxy returns `x-litellm-response-cost`, `estimated` from the versioned price table
(`model_policy/pricing.py`), else `unknown` with `cost_usd=None`. When the provider returns no usage, `usage_known=False`
and consumers must store token counts as null (the int fields are placeholders). `RunBudgets.summary(run_id)` sums known
usage only and sets `incomplete=true` when any call lacked usage or cost; a call with unknown usage keeps its worst-case
reservation counted against the budget. Bump `PRICING_VERSION` whenever rates change.

## Alert thresholds and monthly caps (starting points)

- Monthly: keep `GLOBAL_BEDROCK_MONTHLY_LIMIT_USD` (default 5.00). Alert at 50%, 80%, 100% of it via the existing AWS Budget.
- Per run: `RUN_MAX_TOKENS` about 20000 and `RUN_MAX_USD` 0.05 for the POC; alert on any `run_budget_exhausted` spike.
- Watch: share of `cost_kind=unknown` or `usage_known=false` above 1%; any `fallback_used`; `model_not_entitled` > 0.

## Rollback

1. Remove `MODEL_POLICY_FILE` (and `RUN_MAX_*`) and redeploy: back to plain planner/answer aliases.
2. `LLM_PROVIDER=bedrock` bypasses the proxy and the policy layer entirely (DynamoDB guard still applies).
