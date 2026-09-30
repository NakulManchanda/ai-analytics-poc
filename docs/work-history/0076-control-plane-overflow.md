# Work history 0076 — Control plane Slice 4: overflow policy (#122)

## Goal

Final #122 slice: explicit, testable overflow for local capacity failures, without masking local reasons or overflowing tenant limits.

## Starting point

- Slices 1-3 (#144, #145, #146) placed, admitted, and queued requests; every local capacity failure was returned to the caller.

## Decisions

- Eligible only for 503 and 529 with `never_overflow` false: admission capacity sheds (`no_signal`, `kv_pressure`, `decode_slots`), `queue_full`, `timeout_queue`,
  placement `no_healthy_worker`, worker connect failure (`worker_unavailable`), and upstream 503/529 (`worker_overloaded`).
- Never overflow: 429 tenant limits, 500/502, 504 `deadline_unachievable`, guard 4xx, `slice_oom`, and local config errors (`unknown_forced_worker`, `unknown_policy`).
- Destination is configuration only: `OVERFLOW_ENABLED` (off by default), `OVERFLOW_PROVIDER`, `OVERFLOW_MODEL`, `OVERFLOW_URL` (OpenAI-compatible), `OVERFLOW_API_KEY` from a Secret.
  Disabled or misconfigured behaves exactly as before. Real provider integration is out of scope.
- One overflow attempt, never after local stream bytes start; if overflow fails the original local error is returned. Headers `x-overflow`, `x-overflow-reason`; `x-place-decision: overflow`.
- Streaming: the local upstream stream is opened before the response is returned so a 503/529 is known before the 200 status is committed; a pre-open connect error is now a 503 JSON error, not an SSE error event.
- Metrics `orch_overflow_total{reason,provider,model,outcome}` and `overflow_error_total{reason}`; original reason preserved. API key never logged, returned, or exported (tested).

## Verification

- `uv run --project services/app pytest infra/inference/tests tests -q` — 209 passed (new overflow tests).
- `black --check` / `ruff check` at CI scope — clean.
- Not verified: a real overflow provider, live cluster. Streaming overflow `ok` is recorded when the overflow stream opens, not when it ends.

## PR / merge state

Draft PR (this change). Completes the four planned #122 slices; next are #133 and #123.

## Lessons

- Overflow on a streaming path requires deciding before the first byte, which forced opening the upstream stream ahead of the response.
