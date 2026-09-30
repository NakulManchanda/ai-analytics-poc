# Inference evidence index (#123)

Maps every instructor question in the #123 evidence-matrix completion gate to the concrete
artifact that will prove it, the run that produces it, and its **current** status. No run
results are recorded here: nothing below is evidence until the named artifact exists.

Status values: `available-now` (committed code/dashboard/alert exists; the artifact is
reproducible offline or already committed), `pending-run` (needs a cluster run by a human;
the tooling exists), `blocked-on-#133` (needs real KV transfer, which is not built).

Dashboards are the generated Grafana dashboards in
`infra/inference/observability/grafana/dashboards.py` (JSON under `.../grafana/dashboards/`);
alerts are in `infra/inference/observability/prometheus/alerts.yaml`.

## Evidence scope rule

Every value states its scope. **Per-request** values come from that request's own record
(`requests.jsonl`, gateway decision-log lines joined by `request_id`, response headers).
**Window** values are isolated-window Prometheus aggregates (`summary.json`
`prometheus_window`, Grafana panels, `--metrics-url` deltas). **Cumulative** values are raw
counter scrapes. A window or cumulative value is never attributed to a single request, and an
aggregate counter is never used to infer one request's cache hit, hop or engine stage.

## Question to evidence

| # | Instructor question | Artifact that will prove it | Produced by | Owner | Status |
|---|---|---|---|---|---|
| 1 | What is shared vs unique in the prompt? | Exact-token region summary: `manifest.json` `system_prefix.exact_tokens` (gateway `/tokenize`) for `e3_routing_mixed`; growing multi-step trace `requests.jsonl` (`tokens_in` per step); prefix identity `x-prefix-id` in gateway decision log; `docs/prefix-contract.md` | E2/E3 runs of `e3_routing_mixed` | #115 | pending-run (prefix contract and canonical prefix committed) |
| 2 | What dies at guard vs admit vs place vs queue? | Counters `guard_reject_total`, `orch_shed_total{reason}`, `placement_error_total`, `queue_error_total`; dashboards "Gateway & Admission", "Router & Placement", "Queues"; one correlated request per stage via `trace_request.py` (`admission`/`placement`/`queue` status `rejected`) | E4 run (`e4_admission_overload`) plus guard/placement error probes | #122 | pending-run |
| 3 | Where is timeout prevented? | One admission `deadline_unachievable` case (`x-admit-decision: shed:deadline_unachievable`, gateway log `admission_inputs`) and one `queue_error_total{reason="timeout_queue"}` case, each traced with `trace_request.py` showing `engine` = `not_reached`; vLLM waiting count flat in the same window | E4 run (tight `deadline_ms` turns) | #122 | pending-run |
| 4 | Where is KV protected? | Gateway log `admission_inputs` (KV/headroom/tokens-in-flight) plus decision and outcome for a request near saturation; worker KV time series in "KV & Prefix Cache" and "Memory Proof" dashboards; alert `InferenceKVPressureSustained` | E0 saturation run + E4 | #122/#123 | pending-run |
| 5 | Where is interactive traffic prioritized? | `orch_queue_wait_seconds` and `gateway_ttft_seconds` by `class`; `summary.json` `by_workload_class` goodput and `queue_wait_ms`; dashboard "Queues"; batch-starvation check from per-class request counts | E4 run | #122/#123 | pending-run |
| 6 | Where is tenant domination prevented? | Noisy-tenant local `429` counts (`orch_tenant_total`, `summary.json` `by_tenant`), fairness calculation in the notebook, preserved interactive goodput | E4 run (`tenant_noisy`) | #122/#123 E4 | pending-run |
| 7 | Where does hop occur and what is not copied? | Real block/token provenance, `hop_*` fields/metrics (`hop_total`, `hop_tokens_total`, `hop_bytes_total`, `hop_duration_seconds`), destination consumption, explicit excluded state (weights, request bodies, output, scheduler state); `trace_request.py` `hop` stage `confirmed` only when every proof field is present (source/destination provenance, prefix identity and compatibility namespace, tokens and bytes > 0, duration, `hop_result: transferred`, `confirm_result: available`, observed destination reuse > 0); otherwise `attempted_not_confirmed` with the missing fields named. The fields must also be mutually and externally consistent: source differs from destination, destination equals the request's chosen worker, reused tokens do not exceed transferred tokens, `router_prefix_id` equals the request's `x-prefix-id` (correlation only; `x-prefix-id` is a router affinity label, never the canonical identity), `prefix_identity` equals the manifest's `canonical_prefix_identity`, and `compatibility_namespace` equals the manifest's `compatibility_namespace` exactly. Failed checks and unverifiable ones (missing correlated or canonical evidence, for example `unverifiable: canonical prefix identity`) are listed and block `confirmed`. The canonical-evidence contract is an assumption until #133 defines it. The field names (`HOP_PROOF_FIELDS` in `app/benchmarks/trace.py`) are an assumption until #133 lands. Dashboard "KV Hop (stub)" is a placeholder until #133 exports metrics | E5 real-transfer treatment | #133/#123 E5 | blocked-on-#133 |
| 8 | Where does eviction occur and what becomes a ghost? | Eviction/invalidation event plus a stale-metadata/ghost-cache failure or prevention test (`kv_evict_total`, `kv_directory_*`); same-worker miss case `e5_local_eviction_4k` only supplies the workload, not proof of eviction | #133 tests + `e5_local_eviction_4k` with vLLM prefix-cache/KV evidence | #133 | blocked-on-#133 (workload available-now) |
| 9 | Where is the gateway boundary vs the engine scheduler? | `trace_request.py` timeline: per-request gateway stages (admission, placement, queue wait, dispatch to release) beside window-level vLLM queue/running/prefill/decode panels ("Scheduler & Concurrency", "Prefill vs Decode"). Engine wait is not separable per request; it is labeled window-level. The trace's `post_first_token_ms` (e2e minus TTFT) is client-observed, not vLLM decode; per-request engine prefill/decode timing is unavailable (window-level only) | Any gateway run with `--metrics-url` and a pulled `gateway.log` | #122/#123 | available-now (tool and log fields); pending-run for a real trace |
| 10 | What limited concurrency for the real app? | Taxi-agent saturation run at measured context lengths: `sweep.csv` (throughput vs goodput vs offered concurrency), first-limiter classification from "Scheduler & Concurrency", "KV & Prefix Cache", "GPU & HAMi Slices" | E0 run (`--sweep-concurrency`) | #123 E0 | pending-run |
| 11 | Which four production alerts? | `alerts.yaml`: `InferenceKVPressureSustained` (KV pressure), `GatewayInteractiveTTFTSLOBreach` (interactive SLO; `InferenceEngineTTFTHigh` is its engine-side companion), `GatewayQueueShedSurge` (queue/shed surge), `InferenceWorkerIntegrity` (worker/engine/scrape integrity; hop integrity needs #133 metrics). Threshold rationale still needs observed values | Committed rules; thresholds validated in E0/E4 | #123 | available-now (rules); pending-run (threshold rationale) |
| 12 | Which pool should scale: prefill or decode? | Same-window uncached prompt rate, cache hits, prefill time, TTFT vs running slots, generation rate, decode/ITL and goodput ("Prefill vs Decode", "Scheduler & Concurrency") | E0 and E2 runs | #123 | pending-run |
| 13 | What changes at 10x, and which knobs are wrong? | Trace-driven 10x discussion grounded in the E0 bottleneck; explicit assessment of `max_num_seqs`, context allowance, and adding replicas | Notebook after E0/E3/E4 | #123 | pending-run |

## Experiment to artifact

| Experiment | Runs | Artifact | Status |
|---|---|---|---|
| E2 prefix reuse (local vs independent destination) | `e5_local_reuse_<size>`, `e5_destination_hit_<size>` (reported separately) | per-run `summary.json`, `prometheus_window` hit deltas (window-level), per-request TTFT | pending-run |
| E3 routing | `make replay-e3-least-loaded` / `replay-e3-prefix-then-load` | `manifest.json`, `summary.json` `by_worker`, `placement_reason_by_turn` | pending-run |
| E4 admission | `make replay-e4-admission-on` / `replay-e4-admission-off` | `summary.json` goodput, sheds by reason | pending-run |
| E5 recompute control | `e5_recompute_control_{1k,2k,4k,7k}` (A to B, transfer disabled) | TTFT per request, `x-intended-action`, window prefix-cache deltas | pending-run |
| E5 real transfer | same four sizes, same conditions | hop provenance and metrics (#133) | blocked-on-#133 |
| Single-request proof | any run plus pulled `gateway.log` | `trace_request.py` output/JSON | available-now (tool), pending-run (real request) |

## Prefix locality and mobility matrix

| Case | Placement | Scenario | Status |
|---|---|---|---|
| Same-worker local reuse | A to A | `e5_local_reuse_<size>` | pending-run |
| Same-worker eviction/miss | A to A after filler pressure | `e5_local_eviction_4k` (eviction unverified by the scenario; inconclusive unless vLLM evidence shows it) | pending-run |
| Cross-worker recompute | A to B, no transfer | `e5_recompute_control_<size>` | pending-run |
| Independent destination hit | B warmed separately, then A-origin continuation to B | `e5_destination_hit_<size>` | pending-run |
| Real KV transfer | A to B with transfer | not implemented; same sizes to be reused | blocked-on-#133 |
| Policy under contention | policy-selected | E3 traces plus a contention variant | pending-run |

## Unsupported claims (never make these)

- A worker change, a latency drop, or a prefix match is **not** proof of a KV hop. Only a
  logged transfer with provenance, prefix identity/namespace, tokens and bytes, result and
  destination confirm/consumption is.
- Client-observed time after the first token is not vLLM decode time.
- `x-intended-action` (`local_reuse`, `destination_hit`, `recompute`) is the router's belief.
  It is not observed cache reuse. `hop` is never produced before #133 confirms a transfer.
- Observed reuse for one request requires per-request evidence. Prometheus hit/query deltas
  are window-level and must not be assigned to a request.
- A forced placement (`x-force-worker`) proves routing only. It says nothing about whether the
  destination had the prefix.
- `destination_hit` cases show B reusing its own cache; they are not A-origin transfers.
- Same-physical-GPU contention (Worker A/B are slices of one A100) is a disclosed limitation.
- Synthetic padded prefixes (E5, `e3_routing_large_prefix`) are prefix-size treatments, not
  the taxi-agent prefix. Cite the exact token count from `manifest.json`, not the label.
- Negative or inconclusive results are reported as such.
