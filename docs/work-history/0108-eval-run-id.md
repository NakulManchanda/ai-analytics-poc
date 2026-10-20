# 0108 — Opt-in eval_run_id (#189 PR-B)

## Goal
Let a baseline be selected by one label instead of an external manifest: an opt-in `eval_run_id` carried from the request onto the durable run, every run event and every capture record.

## Starting point
`POST /api/runs` accepted only `prompt` and `conversation_id`; nothing tied runs of one batch together.

## Decisions
- `RunRequest.eval_run_id` is validated with `^[A-Za-z0-9._:-]{1,64}$` (empty, oversized or other characters give 422). Header `x-eval-run-id` is the fallback, same validation (invalid gives 422); the body field wins.
- The router passes `eval_run_id` to `prepare_run` only when set, so default calls are identical to before.
- Stored as the additive `Run.metadata["eval_run_id"]` (no DynamoDB schema change); the five terminal `Run` rewrites in the loop carry it forward.
- `RunEvent.eval_run_id` is optional and serialized (`to_dict`, `to_sse`) only when set; `from_dict` reads it back. Golden event shapes are unchanged.
- capture.v1 gains an optional `eval_run_id` on every record, emitted only when set (documented in `docs/capture.md`). `tooling/evals` `TraceRecord` ignores extras, so it is untouched.
- `CallContext` gained an optional `eval_run_id` (default None) so PR-C can read it; it is not used yet.
- `ai.eval_run_id` is set on the `ai.run` span when present (span now owned by `execute()` after #196); absent by default.

## Verification
- New `services/app/tests/test_eval_run_id.py` (13 tests): malformed/empty/oversized body and header rejected, header fallback, body precedence, absent passes nothing, event serialization, and a full loop run showing the id on Run.metadata, every event, every capture record (model and tool call) and the call context; absent leaves no key anywhere.
- `services/app` 417 passed; `tests` 93 passed, 1 skipped; `infra/inference/tests` 396 passed; `tooling/evals` 89 passed, 2 skipped; `black --check` and `ruff check services/app` clean.

## Limitations
- LiteLLM tagging is PR-C; live proof is PR-D. No live or Bedrock run.

## PR / merge state
See the PR for #189 PR-B.

## Lessons
- Emitting optional fields only when set keeps default shapes byte-identical without a schema version bump.
