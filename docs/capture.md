# Run capture (`capture.v1`)

Issue #169. Records model-call and tool-call evidence per run for evaluation and review.
Disabled unless `CAPTURE_DIR` is set.

## What is captured

One JSON object per line, one record per model call (`record_type=model_call`) or tool call
(`record_type=tool_call`), joined by `run_id`. Written once per run from durable state when
`OrchestrationLoop.execute()` finishes (success, failure, or cancellation).

- Common: `schema_version`, `run_id`, `turn_id`, `step`, `call_id` (idempotency key), `tenant_id`,
  `trace_id`, `conversation_id`, `prompt_template_version`, `run_status`.
- Model call: `role` (planner|answer), `model_alias` (null until #170), `provider_model_id`,
  `input_tokens`/`output_tokens`/`reasoning_tokens`, `ttft_ms`, `latency_ms`, `finish_reason`,
  `error`, `cost_usd`, `cost_kind`, `question_sha256`, `answer_sha256`.
- Tool call: `tool_name`, `tool_call_id`, `arguments`, `query_id`, `status`, `row_count`.
  Tool result rows are never captured.

Unknown token counts are `null`, never `0` (a recorded `0` input/output count is treated as unknown).

## Privacy defaults

Metadata only. `question` and `answer` are `null` (hashes only) unless `CAPTURE_CONTENT=1`, which
is for opted-in demo/eval traffic only. Raw provider prompts/responses are not captured by the app
(LiteLLM-side capture stays under `LITELLM_CAPTURE`). Text fields are capped at
`CAPTURE_MAX_FIELD_BYTES` (default 4096). Captured questions may contain user data; treat the
directory as sensitive and store it on encrypted disk.

## Where it lives

`$CAPTURE_DIR/<tenant_id>/captures.jsonl`, append-only, one directory per tenant (`tenant_id`
is validated; the app currently uses `default`). Duplicate `(run_id, call_id)` writes are ignored.
Write failures never block the response; they are counted (`CaptureRecorder.dropped`) and logged.

## Export and purge

```python
from pathlib import Path
from app.capture import export_jsonl, purge
export_jsonl(Path("captures"), "default", Path("out/captures.jsonl"))  # sorted by (run_id, step)
purge(Path("captures"), "default", run_id="run_...")  # one run
purge(Path("captures"), "default")                    # whole tenant
```

Exported records are unverified model output, not ground truth; label through human review.
Retention is manual: purge on a short schedule.
