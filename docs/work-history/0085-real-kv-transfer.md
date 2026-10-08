# Work 0085 — Real cross-worker KV transfer

## Milestone

Inference track — Issue #133

## Branch

`feat/133-kv-transfer`

## Worktree

`.worktrees/133-kv-transfer`

## Goal

Make compatible KV blocks reusable across the two existing HAMi/vLLM replicas through a real
LMCache/Mooncake path, while keeping placement intent, local cache reuse and transferred-block
consumption distinguishable.

## Starting state

The gateway could place a continuation on either worker and held time-bounded prefix-location
beliefs. Both workers had private vLLM prefix caches. There was no transfer backend, compatible
block identity, lifecycle contract, hop telemetry or real-hop proof runner.

## Changes

- Added canonical token-derived prefix and compatibility identities plus a backend-neutral
  lookup/transfer/confirm/invalidate contract.
- Added a TTL/capacity-bounded metadata directory with worker-generation and namespace
  invalidation. Directory results are explicitly metadata-only.
- Added a vLLM 0.11.0 dynamic connector around LMCache 0.3.9. It rejects incompatible cache
  namespaces, partial retrieval and unverified bytes, and confirms consumption after the model
  forward/save boundary.
- Added bounded worker metrics and structured per-request evidence without request, conversation
  or prefix IDs in metric labels.
- Added an opt-in CPU/TCP Mooncake image and renderer that retains the two 50/50 HAMi workers.
- Added a four-case live smoke that retains topology, versions, requests, raw metric windows,
  worker logs and validation output.

## Key decisions

- Use LMCache's native Mooncake Store backend rather than a metadata-only simulator.
- Use TCP and host memory for the initial same-host HAMi topology. This preserves the contract if
  Worker B later moves to another GPU host without requiring RDMA for the first proof.
- Pin vLLM 0.11.0 and LMCache 0.3.9, the documented compatible pair. Keep Mooncake internal to
  the cluster and use a fresh cache namespace for every controlled run.
- Treat scheduler cache allocation as availability. Only the worker's completed forward boundary
  proves transferred KV consumption; local-cache controls additionally require an isolated vLLM
  prefix-hit counter window and a successful response.

## Verification

- `uv run --project services/app pytest infra/inference/tests tests/inference -q` — 299 passed
- `uv run --project services/app ruff check ...` — passed
- Independent review fixes: filtered intermediate `kv_hop_available` events in metrics to avoid double-counting, aligned gateway reasons (`local_prefix_present`, `no_prior_worker`, `experiment_on`, `experiment_off`) with runtime/metrics allow-lists, removed pre-retrieval deadline abort in `retrieve()` to avoid dropping multi-request forward batches after external blocks are committed, and added a test for post-schedule deadline behavior.
- Live two-worker GPU proof: pending a new inference instance.

## Pull request

Draft PR #154 open.

## Known limitations

- The local machine cannot prove CUDA/HAMi/Mooncake behavior. Issue #133 remains open until the
  retained smoke artifacts show positive transferred tokens/bytes and destination consumption.
- The opt-in bundle temporarily enables forced placement and experiment controls. Operators must
  restore the normal gateway manifest after a run.

## Next session handoff

1. Provision the isolated A100/k3s lab and confirm both HAMi workers are healthy.
2. Build and push the pinned image with `make inference-kv-image`, then render the opt-in bundle
   with a fresh cache namespace. Review the YAML before applying it; the renderer never applies.
3. Copy `infra/inference/mooncake/topology.example.json` and `versions.example.json` into `work/`,
   replace every placeholder from the live cluster, and keep the cache namespace identical to the
   rendered bundle.
4. Run `make inference-kv-smoke` where `kubectl`, the gateway, and both worker endpoints are
   reachable. Retain the output under `metrics/inference/<run-id>/`.
5. Accept the run only if `validation.json` passes all four cases and the real-transfer event has
   positive `transferred_tokens`, positive `transferred_bytes`, Mooncake provenance, and
   `destination_consumed: true` after the forward boundary.
6. Attach the retained evidence to issue #133, update this work history and the draft PR, restore
   the normal gateway manifest, and tear down the paid GPU environment.

If image startup or the smoke fails, begin with the worker logs and `kv-events.json`. Keep the
failure artifacts; do not infer a hop from latency or placement headers. The most likely live-only
repair surface is the exact vLLM 0.11.0/LMCache 0.3.9 connector API or Mooncake service startup,
not the gateway decision path.

## Lessons

Placement, an independently warm destination and a shared-store retrieval are three different
events. Evidence must be collected inside the cache/forward path; latency and worker headers are
insufficient.
