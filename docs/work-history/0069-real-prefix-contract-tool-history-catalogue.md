# Work history 0069 — Real prefix contract, tool-message history, and catalogue-based tool selection (#115 slice C)

## Goal

Fix the concrete bug where a follow-up question (e.g. "compare that with the second highest zone")
gets no memory of the prior turn's ranking, because `ServeLLMClient.answer_with_query_result` only
ever sent `[system, current_tool_result]` to the model. Also add a fixed query catalogue so the 16
UI sample-question chips run deterministically without depending on the model to pick the right
governed tool, and close a related gap: the orchestration loop only dispatched 2 of the 6 governed
MCP tools slice A added, so most catalogue entries would have been rejected as an "invalid tool
proposal".

## Starting point

- `services/app/app/llm.py` `ServeLLMClient.answer_with_query_result`/`propose_taxi_query` built a
  single-turn `[system, user]` payload from `prefix.py` partitions; prior turns were never sent.
- No `role="tool"` message existed anywhere in `state/repository.py` — only `user`/`assistant`.
- `orchestration/loop.py`'s tool-execution step (`_execute_loop`) only recognized
  `query_taxi_data` and `average_trip_metrics`; slice A's `describe_taxi_dataset`,
  `list_taxi_dimension_values`, `aggregate_taxi_data`, `compare_taxi_segments` existed at the
  MCP/`mcp_client.py` layer but had no dispatch branch in the loop, and `parse_query_proposal`
  only validated the original 2 tool names.
- No token-region counting existed beyond the word-count `estimate_tokens()` heuristic; no
  `/tokenize` route existed on the inference gateway.

## Decisions (D16-D19, `.vscode/myfiles/115-react-workload/decisions.md`)

- D16: Sonnet 5 designed and dispatched slice C' directly.
- D17: exact token-region counts use vLLM's OpenAI-compatible `/tokenize` endpoint through the
  gateway, serve mode only; unavailable → `token_count_unavailable_reason` set, never estimated.
- D18: tool observations are now persisted as `role="tool"` conversation messages on tool
  completion, so the renderer can reconstruct the real growing prefix from stored history.
- D19: the catalogue matches only the top-level, normalized question text (16 chips + Dataset
  extras); a follow-up like "second highest zone" is never a catalogue entry and always goes
  through the model, which now has real prior-turn history.

## What changed

- **`orchestration/loop.py`**: persists a `role="tool"` message (sanitized/extended-sanitized
  result as JSON) immediately after the tool-completed step, before context reduction. Added a
  fixed-catalogue lookup before the model tool-proposal call (`tool_source="catalogue"` on hit,
  `"model"` on miss, recorded on `LLMCall`/`RunStep` metadata alongside `finish_reason`/
  `cost_source`). Generalized Step D's tool dispatch to all 6 governed tools: `query_taxi_data`/
  `average_trip_metrics`/`compare_taxi_segments` keep the existing strict
  `sanitize_query_result` schema (their envelopes match it exactly); `describe_taxi_dataset`/
  `list_taxi_dimension_values`/`aggregate_taxi_data` get a new, lighter
  `sanitize_extended_governed_result` (bounds-checks `row_count`/`query_id`/`truncated` and an
  8 KiB byte cap) because their envelopes carry extra descriptive fields (`query_class`,
  `code_dictionaries`, ...) that don't fit the row/column-only schema. Extended
  `parse_query_proposal` with per-tool argument validators for all 4 new tools so a model
  proposal naming any of the 6 tools is accepted, not just the original 2.
- **New `prefix_render.py`**: `render_conversation_prompt(conversation_id, new_question, repo)`
  builds `[system(global_shared), *stored_messages_for_that_conversation, user(new_question)]`.
  Deterministic; carries `prefix_contract_version = "v2-conversational"`.
- **`llm.py`**: `ServeLLMClient.propose_taxi_query`/`answer_with_query_result`/
  `stream_answer_with_query_result` now build `messages` from `render_conversation_prompt` when a
  `conversation_id` and `repo` are both supplied (serve-mode calls from the loop); falls back to
  the prior single-turn payload otherwise (e.g. direct/test call paths with no conversation_id).
  Existing `x-prefix-id`/header/`PrefixPartition` machinery is unchanged.
- **New `query_catalogue.py`**: normalized-question → `(tool_name, tool_arguments)` fixed dict
  covering the 16 chips in `web/src/App.tsx` plus the Dataset chips, using the real tool
  names/argument shapes from `mcp_client.py`/`server.py`.
- **`prefix.py`**: added `ExactTokenCounts`/`count_prefix_tokens_exact()` calling the gateway's
  `/tokenize`; never falls back to `estimate_tokens()` for this field.
- **`infra/inference/gateway/main.py`**: added a minimal `/tokenize` pass-through route to the
  worker (it did not proxy this endpoint before).

## Tests

- `test_prefix_render.py`: turn-2-sees-turn-1 regression (the direct fix for the bug), two
  conversations don't leak history, deterministic + versioned rendering, no-conversation-id
  fallback.
- `test_query_catalogue.py`: normalization, chip hits, follow-up misses.
- `test_orchestration_loop.py`: catalogue hit skips the model call and records
  `tool_source="catalogue"`; a non-catalogue follow-up records `tool_source="model"` and its
  rendered proposal payload carries real prior-turn context; an extended governed tool
  (`describe_taxi_dataset`) runs end-to-end through the loop (dispatch → sanitize → persisted tool
  message → completed run); `parse_query_proposal` accepts/rejects `aggregate_taxi_data`
  proposals.
- `test_prefix_contract.py`: exact token counts are `None` with a reason when not in serve mode or
  the `/tokenize` call fails; succeed via a mocked gateway call.
- `infra/inference/tests/test_gateway_contract.py`: `/tokenize` proxies to the worker; worker
  unavailable surfaces as 503.
- Updated (not weakened) existing tests whose literal assertions depended on the previously-absent
  tool message or the previously-2-tool-only dispatch: `test_orchestration_loop.py`,
  `test_v11_integration_smoke.py`, `test_v11_state_contract.py` message/step-count assertions now
  expect the additive `role="tool"` message; a handful of tests that specifically exercise the
  model-proposal failure/budget/event-ordering machinery had their literal prompt changed to a
  non-catalogued phrasing (`test_events.py`, `test_governed_query.py`,
  `test_orchestration_loop.py`) since their intent is to test the model call path, which a
  catalogue hit on that exact chip text would otherwise correctly bypass.

## Verification

- `uv run --project services/app pytest services/app/tests -q` → 198 passed.
- `uv run --project services/app pytest infra/inference/tests tests/inference -q` → 61 passed.

## Limitations / unverified

- Live vLLM `/tokenize` behavior (exact request/response shape from a real Qwen3 worker) is
  unverified — no GPU instance running; the implementation follows vLLM's documented
  OpenAI-compatible `/tokenize` contract (`{"count": N, "tokens": [...]}`), and the gateway/prefix
  code fails safe (reason set, no estimate) on any mismatch.
- `count_prefix_tokens_exact` is implemented and tested as a standalone utility; it is not yet
  wired into the per-call `LLMCall`/telemetry persistence path (that would require threading the
  rendered `PrefixPartition`, model id, and gateway URL through the answer/proposal call sites into
  a persisted field) — flagged here rather than guessed at under this slice's scope.
- The context reducer's compact tool-result preview (`reducer.py`) still assumes a
  `rows`/`columns` shape; for the 3 extended tools it silently produces an empty preview (no
  crash, defaults to `[]`) since their envelopes don't carry `rows`. The full raw result still
  reaches the final-answer call unaffected.

Implemented by a Sonnet 5 worker under Sonnet 5 coordination.
