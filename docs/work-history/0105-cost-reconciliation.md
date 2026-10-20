# 0105 — Reconcile app event cost with capture cost (#187)

## Goal
Find why run-event cost was about 96x the capture cost in the frozen `2026-10-20-golden-baseline`, establish which counters enforce which budget, and fix the confirmed bug.

## Starting point
Baseline totals: app events $0.2328, capture $0.0024, LiteLLM proxy $0.0029. Investigation (read-only, frozen evidence untouched) joined the 70 baseline model calls by call id:
- Tokens were identical in events and capture on all 70 calls.
- Event `cost_usd` equalled tokens x $3/M input + $15/M output on all 70 calls: `orchestration/loop.py` applied Claude 3.5 Sonnet rate constants to every non-self-hosted call, while the baseline ran Nova Micro ($0.035/M, $0.14/M).
- Capture `cost_usd` equalled Nova Micro rates on all 70, and the event's own `result_cost_usd` equalled the capture figure, so the correct number already reached the event.
- The LiteLLM $0.0029 was a scope difference: the capture holds 82 model calls ($0.002926 total); 12 belong to earlier slice runs in the same session.
- Event `cost_kind` could say `billed` while `cost_source` said `bedrock_estimate`.

## Budget authorities found
- Monthly global guard (`bedrock_budget.py`): independent of the app estimate. It adds a fixed worst-case reservation (~$0.0045) per call and is not settled to actual cost, so it behaves as a call counter. Left unchanged; whether it should settle to actual usage is a separate design decision.
- Per-run budget tracker: used the inflated estimate (fails closed, but overstated ~96x). Fixed here.
- Model-policy budgets: already per-alias priced.

## Decisions
- `call_cost` now takes the LLM result: a known `result.cost_usd` is the call cost (same figure capture records). Otherwise the call is priced from the model-policy pricing table by `model_alias` (LiteLLM client's table, exposed as `LiteLLMClient.pricing`), then by model id, then by model id in the default table (covers the local fake and direct Bedrock clients on the pinned default model). No hard-coded rate remains; the Sonnet and Nova constants are removed.
- Informational cost is separate from budget accounting. Known/complete cost: the budget is charged that same cost (unchanged). Missing or partial usage with a resolvable price: the event shows the labelled estimate on the reported tokens, but the per-run budget is charged a conservative bound (run `max_input_tokens` plus the call's `configured_max_tokens`, else run `max_output_tokens`, at the resolved price; never below the estimate) and the call is flagged `cost_indeterminate`. No resolvable price: the run fails closed; the call is recorded, and the next LLM call is refused with `budget_exceeded` and `details.limit = "cost_indeterminate"`.
- `cost_source` vocabulary: `provider_billed`, `policy_estimate`, `fallback_estimate`, `estimate_incomplete_usage` (missing input/output usage), `unpriced_model`, `self_hosted`. `LLMCall` also records `budget_cost_usd`, `budget_cost_basis` (`reported`, `conservative_bound`, `unresolvable`, `self_hosted`) and `cost_indeterminate`. `cost_kind` and `cost_source` now agree. Field names are unchanged; the old `bedrock_estimate` value is gone (only parameter defaults in `metrics.py` still name it).
- The per-run default budget (0.5 USD) is now a real $0.50 rather than ~96x inflated; not retuned.

## Verification
- `uv run --project services/app pytest services/app/tests` -> 397 passed; `pytest tests` -> 93 passed, 1 skipped; `pytest infra/inference/tests` -> 396 passed; benchmarks -> 133 passed.
- `black --check services/app tests services/gateway` clean; `ruff check services/app` clean.
- New tests: Nova call at 537 in / 504 out records ~8.9e-05 (not 0.009171); billed/estimated labels; alias-priced fallback; missing-usage conservative bound and fail-closed under a tight limit; unpriced model refuses further calls; per-run budget exhaustion with corrected costs. The cancellation test's mock model id became the default model id so it is priceable.
- Unverified: no live Bedrock run; real-world agreement with capture is shown only by unit tests and the offline baseline reconciliation.

## Not done (follow-ups)
- App-vs-capture variance alert in the baseline report.
- Monthly guard settlement to actual usage (design decision).
- LiteLLM proxy rows were not re-queried against the 70-call window.

## PR / merge state
See the PR for #187.

## Lessons
- Compare cost on the same call ids and time window before suspecting billing: the gap was a hard-coded model rate plus a scope mismatch.
- A cost figure computed independently of the provider/policy result drifts silently; use the result's cost and label its source.
