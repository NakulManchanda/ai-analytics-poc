# 0109 — LiteLLM correlation headers (#189 PR-C)

## Goal
Make every proxy spend-log row mappable to the app run and, 1:1, to the capture `call_id`, and label eval runs.

## Starting point
The loop generates each model call's id before invoking the client, but the client never saw it. `eval_run_id` was on the call context (PR-B) but unused.

## Decisions
- `model_policy/context.py` gains a second ContextVar with `llm_call_scope(id)` / `current_llm_call_id()`, separate from the frozen `CallContext` (it changes per call, not per run).
- The loop passes the already-generated id: `_run_with_cancellation(..., llm_call_id=...)` scopes it before `copy_context()` so it reaches the worker thread (proposal, proposal retry, crewai calls); the direct streaming/non-streaming answer call is wrapped in `with llm_call_scope(answer_call_id)`. Which ids are generated, and when, is unchanged.
- `LiteLLMClient._prepare_request` adds `x-litellm-spend-logs-metadata` (`run_id`, `call_id`, `attempt`, `eval_run_id?`) and, only for eval runs, `x-litellm-tags: eval_run_id:<id>`. Fail-open. Bedrock-direct and serve clients are untouched.
- `attempt` is set in `_governed` per candidate alias (1 = primary, 2 = first fallback, ...) via a ContextVar around the physical call; retry/fallback behaviour is unchanged.
- The metadata header is sent on every orchestrated model call (it is the 1:1 map, not eval-only); calls with no run/call id (ask path, bare client) are byte-identical to before.

## Verification
- New `services/app/tests/test_litellm_correlation.py`: eval and non-eval headers, no-context, fallback attempts 1 and 2, fail-open builder error, scope propagation into a copied-context thread, and a loop run where request-time call ids equal capture call ids for proposal and answer.
- Live proof is PR-D; no network or Bedrock call here.

## PR / merge state
See the PR for #189 PR-C.

## Lessons
- Scope per-call identity in a ContextVar set before `copy_context()` rather than threading it through every client signature.
