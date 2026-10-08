# Work 0089 — Gateway hardening, step 5

## Milestone

Inference track — hardening before the E4 and E5 cluster runs (follows works 0086-0088)

## Branch

`feat/inference-hardening-step5` (from `main`, not stacked)

## Worktree

`.worktrees/inference-hardening-step5`

## Goal

Make the hop decision depend on prefix size, make `slice_oom` a real stay-local outcome, record warm context for every request, and write down where eviction lives.

## Starting state

The hop was requested whenever a prefix owner differed from the chosen worker and a deadline remained, whatever the prefix size. `slice_oom` appeared only in the overflow policy's local-only set and nothing could produce it. Per-request evidence did not say how long a worker had been warm or whether a request was its first since it became warm. The README did not say where eviction happens, and the metadata directory looked like part of the live path.

## Changes

- `infra/inference/gateway/hop.py`, `placement.py`: the hop is requested only when the prior owner is believed to hold at least `KV_HOP_MIN_TOKENS` (default 1024, a placeholder until the E5 crossover); smaller prefixes are recomputed with the new reason `below_min_tokens` (allow-listed in `kv_transfer/runtime.py` and `kv_transfer/metrics.py`). `PlacementDecision` now carries `prior_reusable_tokens`. An explicit experiment override (`x-kv-hop-mode`) still bypasses the minimum so the E5 proof can force the hop at every size; a short deadline still wins.
- `infra/inference/gateway/upstream.py`, `main.py`, `workers.py`, `metrics.py`: a 5xx worker response whose body reports a GPU out-of-memory failure is classified `slice_oom`, stays local (no overflow), is counted in `slice_oom_total{worker}`, carries `x-upstream-reason: slice_oom`, and makes the worker requalify (`Registry.mark_cold`, which also invalidates a warm probe in flight).
- `infra/inference/gateway/workers.py`, `main.py`, `services/app/app/benchmarks/replayer.py`: every dispatch stamps `x-worker-warm-age-ms` and `x-worker-requests-since-warm` (0 is the first request after the worker became warm), and the replayer records them, with `x-hop-decision`, in `requests.jsonl`, so a slow first step can be attributed to warm-up instead of to the hop.
- `infra/inference/README.md` ("Eviction and ghost entries") and the playbook E5 section: eviction lives in vLLM and LMCache/Mooncake, a ghost cannot become a confirmed hop because the worker needs positive bytes/tokens and confirmed consumption, `MetadataDirectory` is a tested library that is not wired in, and the eviction counters the engine and store expose must be listed in the first live session.
- Tests: `test_gateway_hop.py`, `test_gateway_placement.py`, `test_gateway_slice_oom.py` (new), `test_gateway_warm.py`.

## Key decisions

- Minimum-token rule now, cost model later (decided earlier); the 1024 default is a placeholder.
- A18 (reachable spill signals) needed no new code: the queue-based owner saturation added in work 0088 is already the reachable signal.
- No eviction metric is invented; the live session will show what the engine and store export.

## Verification

- `uv run --project services/app pytest infra/inference/tests tests/inference services/app/tests -q` -> passed.
- `ruff check` on the touched gateway and test files and `black --check services/app tests` clean.
- Nothing ran on a live cluster.

## Pull request

Draft PR to `main`.

## Known limitations

- `KV_HOP_MIN_TOKENS` is uncalibrated; E5 sets it.
- OOM detection depends on the worker returning an error body; a worker that dies without one is seen only as a failed scrape or a connection error.
- Streaming errors that arrive mid-stream are classified from the error body only when the worker replies with a non-200 status.
- No eviction count is exported.

## Next session handoff

Remaining before the GPU session: the Part 5 notebook cell, the hop dashboard and alert, realistic E5 prompts, the arrival-rate mode, the `trace_request` hop check, the E4 calibration step in the playbook, and the overflow provider configuration. See `docs/work-history/0088-inference-gateway-hardening-step3.md`.
