# 0120 — Nova Pro as the planner model (#207)

## Goal
Stop planner tool-proposal failures that Nova Micro could not avoid (several tool calls, grouping by day instead of week part) by running the `taxi-planner` alias on Nova Pro, everywhere (local and deployed config), without a deploy.

## Starting point
All aliases targeted Nova Micro. `planner-single-turn@1.2.0` made Micro answer in one call but group by `pickup_weekday` (7 rows); on Nova Pro (untracked local override) the same query produced one `aggregate_taxi_data` call grouped by `pickup_week_part`.

## Decisions
- `taxi-planner` -> `bedrock/amazon.nova-pro-v1:0`; `taxi-answer` and `taxi-classifier` stay Nova Micro (user chose "everywhere" after seeing the cost: about 20x per question, roughly $0.0036 vs $0.0002; planner call about twice as slow).
- Terraform `bedrock_model_arns` now defaults to, and validation requires exactly, the Nova Micro and Nova Pro foundation-model ARNs. The same variable feeds the ai-app task role and the LiteLLM task role, so both get Pro permission.
- `default_pricing` prices `taxi-planner` at Nova Pro rates ($0.80 / $3.20 per 1M tokens, same as LiteLLM's cost map) and every other alias at Micro; `PRICING_VERSION` bumped to `2026-10-bedrock-nova-v2`.
- Review follow-up: `ALIAS_MODELS`/`MODEL_RATES` replace the hardcoded planner alias; `tests/test_litellm_config_pricing.py` fails when `infra/litellm/config.yaml`, the price table or the Terraform ARN allowlist drift (checked by temporarily remapping the planner to Micro: 2 failures). An alias that is neither mapped nor a priced model id stays unpriced (`None`), so a configured `RUN_MAX_USD` fails closed (a first attempt priced it at the highest known rate; review found that is not a safe bound for arbitrary or future models, so it was removed). `pyyaml` added as a dev dependency for the test.
- Not done: `terraform apply`, proxy redeploy, live eval-all on Pro. Existing eval baselines were measured on Micro and are not comparable.

## Verification
- `uv run pytest -q` in `services/app`: 489 passed; `pytest tests/infra`: 21 passed; ruff and black clean.
- `terraform fmt -check` and `terraform validate` (no backend): success.
- Deployed proxy: the user reported on 2026-10-29 that Nova Pro invocation through the deployed LiteLLM proxy was validated; **no evidence (run id, spend-log cost, streaming result, logs) is attached yet**, so this PR does not claim it. Checklist in `docs/litellm-proxy.md`.
- Controlled Micro-vs-Pro golden eval: **not run** (paid Bedrock, needs EVAL_CONFIRM); procedure in `docs/evals.md`.
- Manual (earlier, untracked override): Nova Pro planner returned one grouped call on the weekday-vs-weekend query.

## PR / merge state
Branch `feat/207-nova-pro-planner`.

## Lessons
- A model listing is not access: Anthropic models listed as available but returned "not available for this account"; check an invoke path (IAM simulator + a real call) before switching.
