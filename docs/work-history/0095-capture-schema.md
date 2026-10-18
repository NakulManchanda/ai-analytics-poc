# Work 0095 — Run capture schema and JSONL export

## Milestone

Epic #167, issue #169 (track A)

## Branch

`feat/169-capture-schema`

## Goal

Correlated, metadata-only run/model/tool capture with idempotent JSONL export.

## Decisions

- One hook: `OrchestrationLoop.execute()` builds records from durable `Run`/`RunStep` state in `finally`, so every outcome is covered and `llm.py` is untouched.
- Disabled unless `CAPTURE_DIR` is set; content opt-in via `CAPTURE_CONTENT=1`.
- Zero input/output token counts map to null (LLMResult has no null for them).
- Tenant isolation by directory; the app has no tenant concept yet, so `default`.

## Verification

- `uv run --project services/app pytest services/app/tests`: 246 passed.

## Lessons / limitations

- `model_alias`, `pricing_version`, `fallback_used` stay null until #170 adds them to `LLMResult`.
- Raw provider prompt/response not captured here; no encryption/retention automation.
