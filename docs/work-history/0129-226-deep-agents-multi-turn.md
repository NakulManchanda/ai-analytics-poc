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

## Verification
- `services/app`: full suite passed (551 passed, 1 skipped live test per the subagent run); new tests: history builder unit tests, a two-turn end-to-end run (turn 2's model input contains the turn-1 user text, assistant text, tool name, arguments and result, no tool-role message, nothing from another conversation), correlation metadata tests. New test file repeated 10 times, no flakes. black and ruff clean.
- Not verified: the live two-turn smoke (`multi-001#0` then `#1`) against real Bedrock and LiteLLM; command in the dev-commands skill.

## Known differences and limits
- Only successful tool calls are persisted as tool messages, so failed calls are absent from history. Assistant text is not truncated, only tool results.
- Comparison asymmetry: Deep Agents with restore has prior-turn context that manual ReAct on the LiteLLM route does not. Report it as such in #228.
- Still deferred: grounding and model-policy parity, Phoenix/OTEL parity, reconciling billed costs for requests finishing after cancellation, streaming.

## PR / merge state
Branch `feat/226-multi-turn-restore`; draft PR referencing #226.

## Lessons
- History must be rendered pairing-free for Bedrock; folding tool results into the next assistant message keeps role alternation without tool-call ids.
