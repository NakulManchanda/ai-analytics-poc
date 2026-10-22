# 0112 — malformed ToolUse classification (#186 Slice A)

## Goal
Make the planner failure on follow-up turns observable and typed, without claiming to fix it: classify the Bedrock "invalid sequence as part of ToolUse" rejection, keep the provider facts, record the failed attempt honestly (null usage and cost, budget not bypassed), and make failure codes visible in eval comparisons.

## Starting point
Baseline 2 ended 7 of 43 turns in `run.failed`. multi-004#1 failed 8 of 8 times with `litellm_proxy_http_424`; the app discarded the response body, recorded no `LLMCall` and charged nothing. I first attributed 6 of the 7 failures to this error; the `failure_code` of each `run.failed` event showed that was wrong (4 `no_tool_call`, 1 `multiple_tool_calls`, 1 424, 1 `mcp_tool_error`), and the #186 comment was corrected.

## Decisions
- Diagnostic (5 replays, ~$0.0008): five identical 424s with distinct Bedrock request ids, planner temperature 0.0, so a same-alias retry is unlikely to help; retry deferred pending evidence.
- Offline reconstruction of the provider-bound request for multi-004#1 (single user message, about 150 tokens, matching LiteLLM's logged 148 prompt tokens): no history, no `tool_use` or `tool_result` blocks, so a history-ordering defect is impossible on this path; no app message-construction defect found. Follow-up turns send the planner no conversation context at all (`conversation_summary: null`), which is #110's problem and the most plausible trigger. Root cause is not verified (raw request and Nova output are unavailable).
- Classification in `ServeLLMClient._post_tool_proposal`: planner calls only, 4xx, exact marker within the first 2 KB of the body; only the category and safe facts (`provider_status`, `provider_call_id`, `attempts=1`) survive.
- No retry, no fallback alias, `_governed` unchanged. The failed attempt is an `LLMCall` with null tokens and cost and `usage_known=False`; the per-run budget is charged the existing conservative bound through `call_cost`/`record_call` (unpriced alias -> unresolvable, fails closed), reported run totals unchanged. The per-run reservation is released; the monthly DynamoDB reservation has no release path and stays reserved as for other failed calls (not verified against a real table).
- Failure-code histogram added to `run-compare` and `run-review` (run only; the baseline file has no events). Grading semantics unchanged.

## Verification
- App suite 448 passed (+19 new), root tests 93 passed 1 skipped, evals 120 passed 2 skipped; black and ruff clean for services/app, tests, services/gateway and tooling/evals.
- Histogram on the real pr201-e2e events reproduces the hand tally.
- Not run: any live Bedrock validation (targeted multi-004#1 replay and a full eval-all are pending the owner's approval).

## PR / merge state
PR #202, merged 2026-10-22 (CI green, reviewed; non-blocking follow-ups logged on #186 and #187: monthly reservation reconciliation, and a `usage_complete` flag so a run-level cost of 0 does not look free). `Refs #186`; the issue stays open.

## Lessons
- Do not generalise a cause from one log line: read the structured `failure_code` per failed run before writing it down.
- Offline reconstruction with the stack's own code plus a cross-check against a logged number (148 prompt tokens) can verify what a request contained without a paid call.
