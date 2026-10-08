# Work 0086 — Gateway hardening, step 1

## Milestone

Inference track — Issue #122 / #123 hardening before E4 and E5 cluster runs

## Branch

`feat/inference-hardening-step1`

## Worktree

`.worktrees/inference-hardening`

## Goal

Make the gateway limits match the engine and make the proof runner trustworthy before the next GPU session (E4 and E5).

## Starting state

Admission, queue and guard limits were code defaults not tied to the worker engine flags (decode slots 32 vs --max-num-seqs 8; inflight 32; prompt limit 32768 vs an 8192 context). The guard trusted the caller header x-estimated-prompt-tokens. A worker counted as warm after one scrape and nothing read it. The KV smoke measured the recompute leg first on a fresh worker with no warm-up.

## Changes

- (a) `infra/inference/k8s/gateway/gateway.yaml` now sets `MAX_DECODE_SLOTS=8`, `WORKER_MAX_INFLIGHT=8`, `MAX_MODEL_LEN=8192`, `MAX_PROMPT_TOKENS=8192`, `KV_FREE_MIN`, `PREFILL_TOKENS_PER_S` and `QUEUE_WAIT_PER_WAITING_S` (the last two are placeholders until the on-host calibration run), `WARM_GATE`, `WARM_MIN_SCRAPES`, `WARM_PROBE_MODEL`; tests in `infra/inference/tests/test_manifests_contract.py` tie them to the worker `--max-num-seqs` and `--max-model-len`. (b) `infra/inference/gateway/guard.py`: a caller header can only raise the prompt estimate, and `prompt + max_tokens` is checked against `MAX_MODEL_LEN` (413, reason `context_window_exceeded`); tests in `infra/inference/tests/test_gateway_guard.py`. (c) Warm gate in `infra/inference/gateway/workers.py` (`WARM_GATE=1`): a worker is warm after N consecutive healthy scrapes and one successful probe request; any failed scrape makes it cold; admission and placement use `Registry.serving_snapshots()`; new counter `warm_probe_total`; tests in `infra/inference/tests/test_gateway_warm.py`. A cold worker is excluded, so a forced-placement request to a cold worker fails as `unknown_forced_worker` and, if every worker is cold, requests are shed as `no_signal`. (d) `infra/inference/mooncake/smoke.py`: warm-up requests per worker before measuring (`--warmup-requests`, default 3; first request TTFT recorded as the cold figure in `manifest.json` under `warmup`), and the recompute/transfer legs alternate order by size index (`leg_order` in `crossover.json`); tests in `infra/inference/tests/test_kv_hop_smoke.py`.

## Key decisions

- Warm = N healthy scrapes AND one probe request; cold workers are skipped (a ramp for a returning worker is the next step, not done here).
- Admission/queue values come from the engine flags.
- Alternating order by size index is a counterbalance, not full repeated ABBA.

## Verification

- `uv run --project services/app pytest infra/inference/tests tests/inference -q` → 341 passed
- `ruff check` clean on the touched files
- No live GPU run

## Pull request

Draft PR pending.

## Known limitations

- `PREFILL_TOKENS_PER_S` and `QUEUE_WAIT_PER_WAITING_S` are uncalibrated placeholders.
- No ramp for a returning worker.
- Admission is not yet class-aware.
- Placement still can bounce conversations (`prefix_overlap_low`).
- The smoke's alternating order is by size, not repeated.
- Nothing is proven on a live cluster.

## Next session handoff

Point to `docs/inference-experiments/inference-run-playbook.md` (pre-flight and E4/E5 sections) and `docs/work-history/0085-real-kv-transfer.md`. Remaining hardening items are the worker ramp, class-aware admission, sticky placement, hop minimum-token rule.
