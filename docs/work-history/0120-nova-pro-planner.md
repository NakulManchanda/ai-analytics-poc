# 0120 — Nova Pro as the planner model (#207)

## Goal
Stop planner tool-proposal failures that Nova Micro could not avoid (several tool calls, grouping by day instead of week part) by running the `taxi-planner` alias on Nova Pro, everywhere (local and deployed config), without a deploy.

## Starting point
All aliases targeted Nova Micro. `planner-single-turn@1.2.0` made Micro answer in one call but group by `pickup_weekday` (7 rows); on Nova Pro (untracked local override) the same query produced one `aggregate_taxi_data` call grouped by `pickup_week_part`.

## Decisions
- `taxi-planner` -> `bedrock/amazon.nova-pro-v1:0`; `taxi-answer` and `taxi-classifier` stay Nova Micro (user chose "everywhere" after seeing the cost: about 20x per question, roughly $0.0036 vs $0.0002; planner call about twice as slow).
- Terraform `bedrock_model_arns` now defaults to, and validation requires exactly, the Nova Micro and Nova Pro foundation-model ARNs. The same variable feeds the ai-app task role and the LiteLLM task role, so both get Pro permission.
- `default_pricing` prices `taxi-planner` at Nova Pro rates ($0.80 / $3.20 per 1M tokens, same as LiteLLM's cost map) and every other alias at Micro; `PRICING_VERSION` bumped to `2026-10-bedrock-nova-v2`.
- Not done: `terraform apply`, proxy redeploy, live eval-all on Pro. Existing eval baselines were measured on Micro and are not comparable.

## Verification
- `uv run pytest -q` in `services/app`: 489 passed; `pytest tests/infra`: 21 passed; ruff and black clean.
- `terraform fmt -check` and `terraform validate` (no backend): success.
- Manual (earlier, untracked override): Nova Pro planner returned one grouped call on the weekday-vs-weekend query.

## PR / merge state
Branch `feat/207-nova-pro-planner`.

## Lessons
- A model listing is not access: Anthropic models listed as available but returned "not available for this account"; check an invoke path (IAM simulator + a real call) before switching.
