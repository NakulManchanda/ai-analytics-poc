# Work history 0078 — #123 Slice B: gateway/router/queue/overflow dashboards and five alert rules

## Goal

Make the #122 control plane observable and give the evidence runs dashboards plus the production alerts required by #123.

## Starting point

- Prometheus scraped only workers, HAMi and DCGM, so no gateway (`orch_*`, queue, placement, overflow) metric was collected.
- The dashboard generator had no gateway panels and its docstring stated that no `orch_*` panels existed.

## Decisions

- Added the gateway as a Prometheus scrape target (`inference-gateway.inference-lab.svc.cluster.local:8080`).
- New generated dashboards: `overview`, `gateway_admission`, `router_placement`, `queues`, `overflow`, `memory_proof` (HBM/KV with running/waiting, request rate, preemptions, prefix-cache hit ratio on a shared axis), and a text-only `kv_hop_stub` pointing to #133. No invented hop metrics.
- Every panel metric must be in the generator allowlist; the gateway group is checked as a subset of the gateway registry. Overview goodput is explicitly a proxy (share of 200 responses); true goodput comes from the harness artifacts.
- Five rules in `observability/prometheus/alerts.yaml` (Alertmanager stays disabled): the four required (KV >85% for 5m, gateway interactive p99 TTFT >100 ms for 5m, shed plus `timeout_queue` surge >0.5/s, worker/target integrity) plus a supplemental engine-side TTFT alert. See Review follow-up.
- `deploy.sh` loads the rules via a single `--set-file serverFiles.alerting_rules\.yml=...` on the Prometheus helm upgrade.

## Review follow-up

Independent review of the draft PR found three issues, addressed here:

- **Integrity alert blind to vanished targets (high):** `InferenceWorkerIntegrity` now also fires on `up == 0` for the gateway/worker jobs, `absent(up{job="inference-gateway"})`, and `(count(up{job="inference-workers"} == 1) or vector(0)) < 2`. Covered by expression tests (promtool still unavailable).
- **TTFT alert could not see the interactive SLO (medium):** added gateway histogram `gateway_ttft_seconds{class}` (buckets around 0.1 s), observed once per request at the first streamed chunk (local or overflow) and, for non-streaming requests, at the 200 response (time-to-response). New `GatewayInteractiveTTFTSLOBreach` alerts on interactive p99 > 0.1 s for 5m; the engine-side alert is kept as supplemental `InferenceEngineTTFTHigh`. Overview dashboard has a TTFT panel. Total: five rules (four required plus one supplemental). Gateway tests prove the histogram is observed for streaming, batch non-streaming and overflow, and not on failure.
- **Worker warmth not exposed (medium):** added `worker_warm{worker}` gauge, updated alongside `worker_health`; shown on Router & Placement. Test covers a healthy-but-cold worker (warm 0, healthy 1).

## Verification

- `uv run --project services/app pytest infra/inference/tests tests -q` — 223 passed; regenerating dashboards produces no diff; black clean; ruff clean on the changed test (20 pre-existing E501 in `dashboards.py`, outside CI scope).
- Not verified: `promtool check rules` (not installed), the `deploy.sh` `--set-file` escaping against a live `helm` run, and rendering in a live Grafana.

## PR / merge state

Draft PR (this change).

## Lessons

- Rules that mix vectors with `or` and label templates need a live Prometheus check before the evidence runs.
