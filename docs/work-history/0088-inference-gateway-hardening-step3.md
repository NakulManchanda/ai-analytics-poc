# Work 0088 — Gateway hardening, step 3

## Milestone

Inference track — hardening before the E4 and E5 cluster runs (follows works 0086 and 0087)

## Branch

`feat/inference-hardening-step3` (stacked on `feat/inference-hardening-step2`)

## Worktree

`.worktrees/inference-hardening-step3`

## Goal

Stop conversations bouncing between workers, stop a burst from herding onto one worker, make admission protect interactive work, and make the serve-path smoke prove that every stage ran.

## Starting state

`prefix_then_load` compared the believed reusable prefix with the whole current prompt, so as a conversation grew it fell below the 0.8 overlap threshold and was moved to the other worker (`prefix_overlap_low`, work 0084). Load ties always chose the first worker and a request was only counted when dispatched, so a burst saw identical load. Admission did not look at the request class. The serve smoke only checked that a response with tokens arrived, and neither pre-flight listed it.

## Changes

- `infra/inference/gateway/placement.py`: sticky owner. A conversation with a KV owner stays on it however much its prompt has grown, and moves only when the owner is saturated (KV used over the limit, in-flight tokens over the limit, or waiting + queued + just-placed requests at `PLACEMENT_SPILL_QUEUE`) and another eligible worker can take it; queue-only saturation also requires that worker to be less loaded. `prefix_overlap_low` is gone. Equal-load ties in `least_loaded` are broken randomly.
- `infra/inference/gateway/main.py`, `workers.py`: a request is counted as `pending` on its chosen worker from the pick until the queue answers, and the load score includes it.
- `infra/inference/gateway/admission.py`: class-aware. Batch work may only use the decode slots minus `BATCH_SLOT_RESERVE` (a fraction, default 0.25); the reserved slots stay free for interactive requests. The shed reason stays `decode_slots`; the admission inputs now record the class.
- `infra/inference/k8s/gateway/gateway.yaml`: `PLACEMENT_SPILL_QUEUE` and `BATCH_SLOT_RESERVE`.
- `scripts/smoke/17_inference_serve.py` with the pure helpers in `services/app/app/benchmarks/serve_smoke.py`: the serve smoke now asserts the four stage headers on an accepted request, runs one tool-calling agent step, and checks that an oversized prompt and a prompt + `max_tokens` over the window are rejected at the guard (413).
- `docs/inference-experiments/inference-run-playbook.md` and `inference-session-runbook.md`: pre-flight runs `make inference-smoke` then `make inference-serve-smoke`, in that order.
- Tests: `infra/inference/tests/test_gateway_placement.py`, `test_gateway_admission.py`, `test_manifests_contract.py`, `services/app/tests/test_serve_smoke_checks.py`.

## Key decisions

- Sticky owner with an explicit move rule, not history-aware prefix identity (a larger change to the prefix contract).
- Class-aware admission by reserving a fraction of slots for interactive requests.
- The smoke helpers are pure functions so the checks are unit tested without a cluster.

## Verification

- `uv run --project services/app pytest infra/inference/tests tests/inference services/app/tests/test_serve_smoke_checks.py services/app/tests/test_serve_llm_client.py -q` -> passed (the suite was run six times to check the random tie-break is not flaky).
- `ruff check` and `black --check services/app tests` clean.
- The serve smoke itself needs a live gateway and was not run.

## Pull request

Draft PR stacked on the step 2 PR; retarget to `main` after it merges.

## Known limitations

- `PLACEMENT_SPILL_QUEUE` (4) and `BATCH_SLOT_RESERVE` (0.25) are uncalibrated.
- Batch work is shed only on decode slots, not on KV pressure or the deadline estimate.
- Placement still keys beliefs on `prefix_id`, which advances with every tool observation; the sticky rule hides this but does not fix it.
- Nothing is proven on a live cluster.

## Next session handoff

Remaining before the GPU session: the hop minimum-token rule, warm context in per-request evidence, reachable spill signals, `slice_oom` detection and the eviction documentation (A24, A25, A18, A15, A32), then the notebook cell, hop dashboard and arrival-rate mode. See `docs/work-history/0087-inference-gateway-hardening-step2.md`.
