# Work history 0075 — Control plane Slice 3: per-worker gateway queues (#122)

## Goal

Third of four #122 slices: the gateway owns priority before work enters vLLM, with bounded per-worker queues and an enforced `timeout_queue`.

## Starting point

- Slices 1 (#144) and 2 (#145) placed and admitted requests, but dispatched immediately to the chosen worker.

## Decisions

- Pipeline is guard -> tenant quota -> admit -> place -> queue -> proxy. vLLM keeps FCFS/preemption/continuous batching; the gateway only gates dispatch slots (`WORKER_MAX_INFLIGHT`).
- Queue ordering (from the class reference queue): interactive before batch, short before long, deadline slack minus aging, then FIFO; `MAX_OVERTAKES` stops batch starvation.
  Evaluated at dispatch time by a min-scan over the bounded queue (`QUEUE_MAX_DEPTH`).
- Failures are 503 with `retry-after`: `queue_full` and `timeout_queue` (actual wait > min(`QUEUE_TIMEOUT_S`, remaining `x-deadline-ms`)); neither is ever dispatched (tests assert no upstream call). Tenant tokens are refunded.
- Queue depth participates in placement load (`2*waiting + running + inflight + queued`).
- Metrics: `orch_replica_queue_depth{worker,class}` (refreshed on scrape), `orch_queue_wait_seconds`, `queue_error_total{reason,class}`, and a `queue` stage duration.
- Slots are held until stream end; `release()` is idempotent and also runs as a response background task so a client disconnect before the stream starts cannot leak a slot.
- Defaults (depth 64, inflight 32, timeout 10s, aging 0.5, overtakes 8, long prompt 2000 tokens) are uncalibrated; #123 tunes them.

## Verification

- `uv run --project services/app pytest infra/inference/tests tests -q` — 170 passed (18 new queue tests).
- `black --check` / `ruff check` at CI scope — clean.
- Not verified: live cluster behavior; non-streaming client-disconnect cancel is tested at queue level, not through TestClient.

## PR / merge state

Draft PR (this change). Slice 4 (overflow) remains.

## Lessons

- A cleanup that lives only in a generator's `finally` never runs if the generator never starts; add a response-level safety net.
