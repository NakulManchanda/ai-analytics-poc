# 0129 — Deep Agents multi-turn restore and correlation-failure visibility (#226, parent #224)

## Goal
Let a follow-up turn on the same `conversation_id` see the prior turns and governed results (#110 evaluation axis), and make correlation-header failures observable (PR #237 review follow-up).

## Starting point
The single-turn Deep Agents strategy (#237) sent only the current prompt. Stored tool messages held the result JSON and `{tool_name, query_id}` but not the arguments. The manual LiteLLM/Bedrock path deliberately sends no stored history (its tool-call pairing rules reject the vLLM-shaped history), so manual ReAct has no prior-turn context on this route.

## Decisions
- `build_history_messages` renders prior turns for the agent as plain, strictly alternating user/assistant messages with no tool-role messages: each stored tool observation is folded into the following assistant message ("Governed result from <tool> (arguments: ...): <result>", result truncated with a marker at 2000 characters). Last 4 turns only; other conversations never included; the current message excluded by id; failed or cancelled turns get a synthetic assistant note; legacy tool messages without arguments omit them.
- The Deep Agents recorder now stores the tool `arguments` in the tool message metadata.
- System prompt: if a reference cannot be resolved from the conversation, ask a short clarifying question rather than guessing a query.
- Correlation: header-injection failures and missing/unparseable billed-cost headers are counted into run metadata (`correlation.header_failures`, `correlation.cost_header_missing`, `correlation_ok`); requests are still sent and runs do not fail.
- No QueryResolver (#110 stays open); `_execute_loop` and the manual path are untouched.

## Review fixes (PR #238)
- History budget (P1): an overall 12,000-character budget (proxy for ~3k tokens) plus per-component caps (user 1,000; assistant 1,500; arguments 500; tool result 2,000; at most 2 observations per turn, newest kept). Turns are taken newest to oldest and the oldest dropped first; an oversized newest turn is shrunk (results, then arguments, then assistant text) so it is always present. The floor (capped user text plus block wrappers) can exceed the budget and is documented. `Run.metadata["history"]` reports turns available/included/dropped, total/budget chars and truncation counts.
- Billing completeness (P2): `correlation_ok` is header integrity only. `billing_complete` is reported separately, with `billed_calls` and `estimated_calls`: it is true only when header health is clean, no billed-cost header was missing or unparseable, and no recorded call was priced from the policy-table estimate (re-review fix). A run with zero recorded model calls is vacuously complete; a request still in flight when a run is abandoned is not recorded (billed-cost reconciliation after cancellation stays open in #226). #228 must not treat a run with `billing_complete == False` as a trustworthy cost measurement.
- Provenance (P2): each historical observation is wrapped in `<untrusted_tool_data source="mcp" tool=... query_id=... arguments=...>`; literal opening/closing tags inside the data are neutralized and attribute values escaped; the system prompt says block content is data, never instructions.
- Semantic multi-turn correctness (P2) is not claimed here: the scripted test only proves history reaches the model. A live multi-turn golden-case evaluation belongs in #228.

## Verification
- `services/app`: full suite passed (566 passed, 1 skipped live test after the review fixes; 551 before); new tests: history builder unit tests, a two-turn end-to-end run (turn 2's model input contains the turn-1 user text, assistant text, tool name, arguments and result, no tool-role message, nothing from another conversation), correlation metadata tests. New test file repeated 10 times, no flakes. black and ruff clean.
- Not verified: the live two-turn smoke (`multi-001#0` then `#1`) against real Bedrock and LiteLLM; command in the dev-commands skill.

## Known differences and limits
- Only successful tool calls are persisted as tool messages, so failed calls are absent from history. Assistant text is not truncated, only tool results.
- Comparison asymmetry: Deep Agents with restore has prior-turn context that manual ReAct on the LiteLLM route does not. Report it as such in #228.
- Still deferred: grounding and model-policy parity, Phoenix/OTEL parity, reconciling billed costs for requests finishing after cancellation, streaming.

## PR / merge state
Branch `feat/226-multi-turn-restore`; draft PR referencing #226.

## Lessons
- History must be rendered pairing-free for Bedrock; folding tool results into the next assistant message keeps role alternation without tool-call ids.
