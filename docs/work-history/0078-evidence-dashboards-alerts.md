# Work history 0078 — #123 Slice B: gateway/router/queue/overflow dashboards and four alerts

## Goal

Make the #122 control plane observable and give the evidence runs dashboards plus the four production alerts required by #123.

## Starting point

- Prometheus scraped only workers, HAMi and DCGM, so no gateway (`orch_*`, queue, placement, overflow) metric was collected.
- The dashboard generator had no gateway panels and its docstring stated that no `orch_*` panels existed.

## Decisions

- Added the gateway as a Prometheus scrape target (`inference-gateway.inference-lab.svc.cluster.local:8080`).
- New generated dashboards: `overview`, `gateway_admission`, `router_placement`, `queues`, `overflow`, `memory_proof` (HBM/KV with running/waiting, request rate, preemptions, prefix-cache hit ratio on a shared axis), and a text-only `kv_hop_stub` pointing to #133. No invented hop metrics.
- Every panel metric must be in the generator allowlist; the gateway group is checked as a subset of the gateway registry. Overview goodput is explicitly a proxy (share of 200 responses); true goodput comes from the harness artifacts.
- Four alerts in `observability/prometheus/alerts.yaml` (Alertmanager stays disabled): KV >85% for 5m, engine-side p95 TTFT >100 ms for 5m, shed plus `timeout_queue` surge >0.5/s, worker/engine integrity (unhealthy state, snapshot age >15 s, preemptions >0.1/s).
- The TTFT alert is engine-side and cannot be interactive-only because vLLM does not label TTFT by class.
- `deploy.sh` loads the rules via a single `--set-file serverFiles.alerting_rules\.yml=...` on the Prometheus helm upgrade.

## Verification

- `uv run --project services/app pytest infra/inference/tests tests -q` — 223 passed; regenerating dashboards produces no diff; black clean; ruff clean on the changed test (20 pre-existing E501 in `dashboards.py`, outside CI scope).
- Not verified: `promtool check rules` (not installed), the `deploy.sh` `--set-file` escaping against a live `helm` run, and rendering in a live Grafana.

## PR / merge state

Draft PR (this change).

## Lessons

- Rules that mix vectors with `or` and label templates need a live Prometheus check before the evidence runs.
