# 0131 — Deep Agents model-policy parity (#226, parent #224)

## Goal
Deep Agents model calls obey the same model policy as manual LiteLLM calls (entitlement, per-call token cap, per-run budget reservation), so #228 does not compare a governed arm with an ungoverned one.

## Starting point
The Deep Agents run called the proxy with `llm.planner_alias` directly and only reserved the app-side monthly Bedrock allowance. The manual path wraps each call in `LiteLLMClient._governed`: entitlement -> per-call cap -> per-run reservation -> call -> commit/release (plus fallback aliases).

## Decisions
- `_governed` is untouched. New public `LiteLLMClient` methods reuse the same runtime objects: `resolve_agent_role` (entitlement; policy alias and cap), `begin_agent_call` (re-resolve and reserve `estimate_tokens(messages) + (cap or 1024)` when a run is bound), `end_agent_call` (commit with actual usage, `None` tokens when usage is unknown) and `release_agent_call` (idempotent, accepts `None`), with the same failure codes (`model_not_entitled`, `run_budget_exhausted`).
- Deep Agents resolves the role before building the agent (a denial ends the run as `run.failed` before any request or `llm.started`), builds `ChatOpenAI` with the policy alias and cap, reserves per call in `on_chat_model_start`, commits in `on_llm_end`, releases in `on_llm_error`, and releases any still-open holds in the run's `finally`. `LLMCall` records the policy alias, `configured_max_tokens` and `fallback_used=False`.
- The cap is sent as `max_tokens` through `extra_body`, the field the manual payload sends. `ChatOpenAI(max_tokens=...)` would emit `max_completion_tokens` and `model_kwargs` is moved back to that field by langchain-openai; whether the proxy maps `max_completion_tokens` for Bedrock was not verified, so the manual field name is used.
- Fallback aliases are intentionally not applied (one request per model call); `fallback_used` stays false.

## Verification
- `services/app`: full suite passed (see PR description for the final count); new `test_deep_agents_model_policy.py` (13 tests): not entitled sends no request, cap forwarded as `max_tokens` and recorded, budget too small fails before the request, commit with actual usage, release on provider error, cancellation and `max_llm_calls` abort, two sequential calls share the run budget, unit tests for the new methods. New and affected files repeated 10x without flakes. black and ruff clean.
- Not verified: any live proxy or Bedrock run, including how the proxy applies the cap.

## Known differences from manual policy
- No fallback aliases: a transient provider error on the policy alias fails the run where manual might retry on another model.
- The reservation estimate covers the serialized messages only, not the tool schemas the framework adds.
- Entitlement is checked once per run up front and again per call; the monthly allowance is reserved before the run-budget reservation, matching manual order.
- An abandoned worker's late request is not committed, so that spend is not in the run budget (billed-cost reconciliation after cancellation stays open in #226).

## PR / merge state
Branch `feat/226-model-policy`, stacked on `feat/226-parity` (PR #239); draft PR referencing #226.
