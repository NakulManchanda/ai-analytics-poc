# E6 — Custom router versus Dynamo KV-aware router: comparison protocol

Status: protocol only. Nothing is deployed; see
[ADR 0011](decisions/0011-dynamo-kv-router-comparison.md) (Proposed). This is stretch work
and does not replace E3 (`least_loaded` versus `prefix_then_load`), which stays the headline.

## Arms

| Arm | Router | Selected by | `--router-label` |
|---|---|---|---|
| A | Custom gateway, `least_loaded` | `--policy-override least_loaded` | `gateway` |
| B | Custom gateway, `prefix_then_load` | `--policy-override prefix_then_load` | `gateway` |
| C | Dynamo frontend, KV-aware (`--router-mode kv`) | separate deployment | `dynamo-kv` |

Arms A and B are E3 runs and may be reused if their manifests match arm C on every input
below; otherwise re-run them in the same session.

## Identical inputs

- Scenarios: `e3_routing_mixed` (headline) and `e3_routing_large_prefix` (synthetic
  large-prefix variant). Same workload hash: `manifest.scenario.sha256` hashes the SOURCE scenario before CLI
  overrides and without the execution-only fields (`policy_override`, `admission_mode`), so it
  is equal across arms A/B/C. Arm-specific settings (policy/admission override, `router_label`,
  `gateway_stream`, strategy, endpoint type, treatment label) live in `manifest.execution`
  with its own `execution.sha256`, which is expected to differ between arms.
- Concurrency levels and seeds: same `--sweep-concurrency` list and repeat count for all
  arms (at least 3 repeats per level; the replayer is deterministic given the file, so
  variation comes from the system, which is what repeats measure).
- SLOs: same `--ttft-slo-ms` and `--e2e-slo-ms`; `max_tokens` from the scenario (128).
- Model, tokenizer and chat-template revisions (`MODEL_REVISION`, `TOKENIZER_REVISION`,
  `CHAT_TEMPLATE_REVISION`), vLLM version, and `--engine-flags` (block size, prefix
  caching on, max model len, gpu utilisation): identical strings in every manifest.
- Topology: two vLLM workers as separate HAMi slices on one A100, same slice sizes.
- Dynamo KV block size must equal the vLLM block size; record both.

Reject an arm-C run as evidence if any of the above differs from arms A/B.

### Parity gate (required)

`unknown` is not a value. Every arm must record concrete `model_revision`,
`tokenizer_revision`, `chat_template_revision`, `engine_flags`, `topology`, `vllm_version`,
`kv_block_size`, `max_tokens`, SLOs and scenario hash; Dynamo arms also need a concrete
`dynamo_version` and numeric `dynamo_kv_block_size` equal to `kv_block_size`. Gateway arms
record `--dynamo-version n/a --dynamo-kv-block-size n/a` explicitly. Set them with flags or env
(`MODEL_REVISION`, `TOKENIZER_REVISION`, `CHAT_TEMPLATE_REVISION`, `ENGINE_FLAGS`,
`VLLM_VERSION`, `DYNAMO_VERSION`, `KV_BLOCK_SIZE`, `DYNAMO_KV_BLOCK_SIZE`).

1. Pass `--require-parity` to every run: it exits 2 before any request, writing no evidence,
   if a field is missing or `unknown`.
2. After the runs, check the set:
   `uv run --project services/app python -m app.benchmarks.parity <A>/manifest.json
   <B>/manifest.json <C>/manifest.json`. Exit 1 and a `PARITY FAIL` line for any incomplete arm or any cross-arm
   difference. A failed check voids the comparison.

   The set checker also enforces arm roles, derived from `manifest.router_label` and
   `manifest.execution.policy_override`: A = `gateway` + `least_loaded`, B = `gateway` +
   `prefix_then_load`, C = a `dynamo*` label with no override. At least one arm of each role
   is required (A/B only, or two A's, fail and name the missing roles). Repeats of a role are
   allowed and expected (at least 3 per level). Any other combination is rejected as an
   ambiguous role. `offered_concurrency_levels` is required, non-empty and must be equal
   across all arms.

## Isolation and warm state

1. One arm at a time. Scale the other arm's frontend and workers to zero; do not run two
   arms against the same GPU concurrently.
2. Cold start per arm: restart workers, wait until ready, then run the same warm-up (one
   pass of a fixed unrelated prompt set, not the trace prefix) and record idle GPU memory.
3. Cache state at the start of each measured run must be the same: restart workers between
   runs, or, if not, run a documented eviction step. The trace's own first turns warm the
   prefix identically for every arm.
4. Alternate arm order between repeats (A, B, C then C, B, A) to spread thermal/host drift.

## Commands (arm C; A and B use existing `make replay-e3-*`)

```text
uv run --project services/app python services/app/scripts/run_scenario.py \
  --scenario e3_routing_mixed --endpoint-type gateway_chat \
  --router-label dynamo-kv --label e3-dynamo-kv \
  --target-url http://<dynamo-frontend>:8000 --metrics-url <one worker /metrics> ...
```

`--policy-override` and `--admission-mode` are rejected for a non-gateway label (exit 2).
The replayer still sends `x-conversation-id`, `x-prefix-id` and similar headers; Dynamo may
ignore them. The endpoint path is the same `/v1/chat/completions`. If Dynamo needs a
different `model` string, that would be a harness change to record, not assume.

## Metrics to compare

| Metric | Source | Scope |
|---|---|---|
| TTFT p50/p95/p99, E2E p50/p95/p99 | replayer client timings (`summary.json`) | per-request, client side |
| Goodput and throughput at each offered load | `sweep.json` / `sweep.csv` | per-request classification, aggregated |
| Failed/rejected turns | `requests.jsonl` status | per-request |
| Queue wait | arms A/B: `x-queue-wait-ms` header (per-request). Arm C: not available per request | see below |
| Worker prefix-cache hits/queries, worker queue time | vLLM `/metrics` deltas per worker (`prometheus_window`) | window-level only |
| Routing distribution across workers | see next section | mixed |
| Dynamo router-side counters (if present) | Dynamo `/metrics` (`dynamo_frontend_*`, `dynamo_component_kvstats_*`) | window-level, names unverified |

Evidence hygiene (existing rule): aggregate worker counters are window-level. Never attribute
a window delta to an individual request or conversation.

## Routing distribution without `x-place-decision`

Dynamo does not emit our decision headers (none found in its docs; verify on the live
deployment). Options, in order of strength:

1. If Dynamo documents or exposes a per-request worker id or annotation, capture it and
   label it per-request. Not currently known.
2. Window-level per-worker deltas: the harness scraper (`--metrics-url`) reads exactly one
   URL per run, so it cannot cover two workers. Take per-worker deltas outside it: `curl`
   each worker's vLLM `/metrics` immediately before and after the run and diff the counters
   (requests, prompt tokens, prefix-cache hits/queries), or run a Prometheus range query per
   worker over the manifest's `started_at`/`ended_at`. Report share per worker. This is
   window-level evidence, not per-conversation placement.
3. Worker-side request logs correlated by `x-request-id` if the worker logs it. Unverified.

Do not compare per-request placement reasons between arms; arm C has none.

## What is per-request and what is window-level

- Per-request: client TTFT/E2E, status, tokens, arms A/B decision headers, goodput
  classification.
- Window-level: everything scraped from `/metrics` (cache hit rate, queue time, GPU usage),
  routing share for arm C.
- "Cache reuse" claims for arm C rest on window-level worker prefix-cache counters only.

## Interpretation guardrails

- Do not claim the custom router is better or worse than Dynamo without same-conditions
  evidence (all inputs above matched, arm order alternated, repeats present).
- Report each difference with its uncertainty (repeat range or bootstrap interval), and say
  the setup is two workers on one shared A100 with one trace.
- Report Dynamo's frontend overhead as part of arm C and state that it is not separated.
- A difference in prefix-cache hit rate is a mechanism hint, not proof that routing caused a
  TTFT difference; other components differ between arms.
- If versions or engine flags could not be matched, the comparison is void, not weak.
- Record unverified items from ADR 0011 that were resolved on the live system.
