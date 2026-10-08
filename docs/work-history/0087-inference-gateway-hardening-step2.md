# Work 0087 — Gateway hardening, step 2

## Milestone

Inference track — hardening before the E4 and E5 cluster runs (follows work 0086)

## Branch

`feat/inference-hardening-step2` (stacked on `feat/inference-hardening-step1`)

## Worktree

`.worktrees/inference-hardening-step2`

## Goal

Stop a returning worker from being slammed, make the evidence checks fail when hardware was not measured, and make the worker-parity script fail when the workers differ.

## Starting state

Step 1 (work 0086) added the warm gate but a worker that returned after an outage received its full share at once. `experiments/evidence.py` defaulted the GPU to an A100 with 40 GB and invented a commit SHA when nothing was measured, accepted a Python client metric as proof of a vLLM scrape, and had no tests. `verify-workers.sh` printed "args identical: NO" but exited 0.

## Changes

- `infra/inference/gateway/workers.py`: a worker that returns after being cold (second warm-up onward) is capped on in-flight plus queued work; the cap rises one step per `WARM_RAMP_STEP_S` while the worker has no waiting requests and enough free KV; `serving_snapshots()` skips a capped-out worker only while another warm worker is open. New `worker_ramp_cap` gauge; `WARM_RAMP_STEPS`/`WARM_RAMP_STEP_S` in `infra/inference/k8s/gateway/gateway.yaml`. Tests in `infra/inference/tests/test_gateway_warm.py`.
- `infra/inference/experiments/evidence.py`: hardware must come from the pulled nvidia-smi CSVs or an explicit `gpu_name` and `physical_hbm_bytes` (recorded as `source`), the GPU must be an NVIDIA device, each pod slice must match the declared fraction, no commit SHA is invented, both workers' vLLM scrapes must carry `vllm:num_requests_running`, and `dcgm.prom` must carry `DCGM_FI_DEV_FB_USED`. Tests in `infra/inference/tests/test_experiments_evidence.py`.
- `infra/inference/scripts/verify-workers.sh`: exits non-zero when args or engine settings differ or cannot be verified; tested with a fake `kubectl` in `infra/inference/tests/test_verify_workers.py`.
- A10 was a check only: `ServeLLMClient` no longer retries a 400 or strips tools (`services/app/tests/test_serve_llm_client.py::test_propose_taxi_query_400_raises_and_sends_exactly_one_request`).

## Key decisions

- The ramp holds on engine signals (waiting requests, free KV), not on a measured p99; a gateway-measured p99 per worker is a follow-up.
- The ramp applies only to a returning worker, so the first warm-up after a gateway start is not throttled.
- If every warm worker is capped out, the caps are ignored instead of shedding.

## Verification

- `uv run --project services/app pytest infra/inference/tests tests/inference -q` -> 366 passed.
- `ruff check` clean on the touched files; `black --check services/app tests` clean.
- Copies of the retained E0 evidence pass the stricter validation; the E3 folder fails only on a missing `raw/responses.jsonl`, a check that existed before this change.
- No live GPU run.

## Pull request

Draft PR stacked on the step 1 PR (base `feat/inference-hardening-step1`); retarget to `main` once step 1 merges.

## Known limitations

- The ramp is untested on a live cluster and its hold rule is not p99-based.
- `evidence.py` still expects `raw/responses.jsonl`, which replayer-based runs (E3) do not write.
- `infra/` is outside CI's black scope and many files there are not black-formatted.

## Next session handoff

Step 3: sticky placement and anti-herding, class-aware admission, a stronger serve smoke and the smokes in the pre-flight. See `docs/work-history/0086-inference-gateway-hardening-step1.md` and `docs/inference-experiments/inference-run-playbook.md`.
