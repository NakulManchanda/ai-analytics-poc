# 0067 — Honest model calls, thinking control, per-call telemetry (#115 slice B)

## Goal

Issue #115 split the ReAct-workload build into four slices. This is **Slice B: honest,
measurable model calls**. Before any deterministic-vs-ReAct work (slice C) or evaluation
harness (slice D) can be trusted, the serve-mode LLM client must stop lying about what the
model actually did: no silent retries, no keyword-guessed tool calls reported as the
model's own choice, no hidden `<think>` output, and no Bedrock-rate cost charged for a
self-hosted vLLM gateway.

## Starting point

`services/app/app/llm.py` `ServeLLMClient` had two behaviours that corrupt measurement:

1. On an HTTP 400 from the gateway, if `tools` were in the payload, it silently stripped
   `tools`/`tool_choice` and sent a second request.
2. When the model returned no `tool_calls`, it picked a tool by keyword-matching the
   response content or the user prompt (e.g. "hour" → `trip_volume_by_hour`), and reported
   that guess as if the model had chosen it.

In addition: `finish_reason` was never captured; Qwen3 `<think>...</think>` reasoning was
shown to users with no separation from the visible answer and no
"time to first visible answer token"; serve-mode run records carried the Bedrock default
model id and Bedrock-rate cost (`orchestration/loop.py` `estimate_cost`,
`metrics.py`/`metrics/runs.jsonl`); and the worker manifests
(`infra/inference/k8s/workers/worker-a.yaml`/`worker-b.yaml`) lacked the vLLM tool-calling
flags, which is why the gateway returned 400 for `tools` in the first place.

## Decisions

- **Fail closed, always.** A non-200 response is `LLMProviderError` with a code; there is
  never a second request. No `tool_calls` is a typed `no_tool_call` failure. Malformed
  `arguments` JSON is `invalid_tool_call`. Neither ever falls back to a keyword guess.
- **Thinking control (D2).** Structured/tool-proposal calls always send
  `chat_template_kwargs: {"enable_thinking": false}`. The final-answer call defaults to the
  same but is switchable via `INFERENCE_ANSWER_THINKING` (`Settings.inference_answer_thinking`)
  so #123 can compare thinking on/off later. `<think>` content is stripped from
  non-streaming responses (`split_thinking`) and, for streaming, a small state machine
  (`ThinkingSplitter`) holds back just enough trailing text across chunk boundaries to
  detect a split `<think>`/`</think>` tag before releasing anything to the stream callback.
  A separate `reasoning_content` field (if a provider sends one instead of inline tags) is
  treated as reasoning directly, without tag-splitting.
- **Telemetry, never estimated.** `finish_reason` and `configured_max_tokens` are always
  captured. `ttft_ms` (first token of any kind) and `first_visible_answer_ms` (first
  non-reasoning delta) are measured directly from the stream, not estimated.
  `reasoning_tokens`/`visible_answer_tokens` are read from
  `usage.completion_tokens_details.reasoning_tokens` when the provider reports it;
  otherwise both are `null` with `*_unavailable_reason: "provider_did_not_report_token_split"`
  — never a word-count estimate.
- **Serve-mode cost and model honesty.** `OrchestrationLoop` now detects
  `isinstance(llm_client, ServeLLMClient)` once per run and uses that to pick
  `cost_source: "self_hosted"` (cost `0.0`) instead of the Bedrock per-token estimate. The
  `Run.model` field is set from the actual served model id (`llm_calls[-1].model_id`, which
  `ServeLLMClient` already populates from the gateway's `"model"` response field) instead of
  the hardcoded Bedrock `DEFAULT_MODEL_ID`. Bedrock and the local-fake client are unaffected:
  neither is a `ServeLLMClient`, so both keep the existing Bedrock-rate cost path.
- **Worker flags.** Both `worker-a.yaml` and `worker-b.yaml` gained
  `--enable-auto-tool-choice --tool-call-parser hermes` in the vLLM args, identically (the
  existing "workers differ only by identity" contract test already enforces this). This is a
  manifest-only change; it takes effect on the next fresh `inference-up` deploy of a GPU
  instance, not retroactively on an already-running cluster. No deploy or Make inference
  target was run as part of this PR.

## Implementation

- `services/app/app/llm.py`: removed the 400-retry-without-tools and the keyword/content
  fallback in `_post_tool_proposal`; added `split_thinking()` and `ThinkingSplitter` for
  non-streaming/streaming think-tag separation; extended `LLMResult`/`ToolProposalResult`
  with `finish_reason`, `configured_max_tokens`, `ttft_ms`, `first_visible_answer_ms`,
  `reasoning_tokens(_unavailable_reason)`, `visible_answer_tokens(_unavailable_reason)`; added
  `chat_template_kwargs` to every serve-mode payload; wired `answer_thinking_enabled` through
  `ServeLLMClient.__init__` and `create_llm_client`.
- `services/app/app/config.py`: added `Settings.inference_answer_thinking` (env
  `INFERENCE_ANSWER_THINKING`, default `false`).
- `services/app/app/orchestration/loop.py`: extended the `LLMCall` record with the new
  telemetry fields plus `cost_usd`/`cost_source` and a `to_metadata()` helper; added
  `is_self_hosted`/`call_cost()`/`current_model_id()`/`current_cost_source()` closures so
  every terminal `Run` (completed, cancelled, budget_exceeded, failed) and every
  `emit_run_metrics()` call carries the actual served model, the correct cost/cost_source,
  and the full per-call telemetry list. The existing `except LLMProviderError as err: raise
  OrchestrationError(err.code, err.retryable, ...)` mapping already covered the new
  `no_tool_call`/`invalid_tool_call` codes with no further change needed — that path was
  already generic.
- `services/app/app/metrics.py`: `format_cloudwatch_emf`/`emit_run_metrics` gained
  `cost_source` and `llm_calls` fields, persisted into the `runs.jsonl` record.
- `infra/inference/k8s/workers/worker-a.yaml`, `worker-b.yaml`: added the tool-calling flags.
- `infra/inference/tests/test_manifests_contract.py`: added
  `test_workers_enable_vllm_tool_calling_with_hermes_parser` asserting both workers carry
  `--enable-auto-tool-choice` and `--tool-call-parser hermes` identically.
- `infra/inference/README.md`: noted the new flags and that they take effect on the next
  fresh deploy.

## Tests

- `services/app/tests/test_serve_llm_client.py`: a 400 raises `LLMProviderError` with exactly
  one HTTP request sent; no `tool_calls` raises `no_tool_call` (never a keyword guess, even
  when the content text mentions a real tool name); malformed `arguments` JSON raises
  `invalid_tool_call`; `chat_template_kwargs` is asserted on the tool-proposal payload;
  `<think>` is stripped from a non-streaming answer and `finish_reason`/token-split
  unavailability are asserted; a streaming test sends `<think>`/`</think>` split across SSE
  chunk boundaries and asserts the callback never receives any reasoning text and that
  `first_visible_answer_ms >= ttft_ms`.
- `services/app/tests/test_orchestration_loop.py`: a `no_tool_call` LLM failure is mapped to
  a persisted `Run` with `failure_code == "no_tool_call"`; a full serve-mode run through a
  mocked `ServeLLMClient` persists the actual served model id (not the Bedrock default),
  `estimated_cost_usd == 0.0`, and `metadata["cost_source"] == "self_hosted"`.
- No existing test encoded the removed fallback behaviour, so no assertions were weakened;
  all prior tests pass unchanged.

## Verification

```
uv run --project services/app pytest services/app/tests -q
# 167 passed (159 pre-existing + 6 llm-client + 2 orchestration-loop tests)

uv run --project services/app pytest infra/inference/tests tests/inference -q
# 50 passed (includes the new worker-flags contract test)

uv run --project services/app ruff check services/app/app/llm.py \
  services/app/app/orchestration/loop.py services/app/app/config.py \
  services/app/app/metrics.py services/app/tests/test_serve_llm_client.py \
  services/app/tests/test_orchestration_loop.py
# All checks passed!
```

## Limitations / unverified

- Live vLLM behaviour (whether the Hermes tool-call parser and `enable_thinking` actually
  behave as vLLM's docs/OpenAPI schema imply for Qwen3-0.6B on a real GPU worker) is
  unverified; this PR only changes manifests/tests locally and does not deploy or run any
  Make inference target. #115's original evidence run (once a fresh GPU instance is up)
  should reconcile the assumed response shapes (`reasoning_content`,
  `completion_tokens_details.reasoning_tokens`) against what vLLM/Hermes actually returns.
- `first_visible_answer_ms`/`ttft_ms` are only populated on the streaming path; the
  non-streaming path leaves them `None` since the whole response arrives as one unit.
- This PR does not touch the ReAct loop, classifier, or prompt renderer (slice C), MCP,
  `dataset_spike`, the gateway, or `web/`, per the slice-B boundary.
