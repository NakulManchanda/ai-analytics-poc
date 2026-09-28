# Work history 0066 — Generated per-worker vLLM KV/prefill/decode dashboards (#115 slice E)

## Goal

Replace the thin, class-derived `vllm.json`/`cluster.json` Grafana dashboards (queries that never
split worker A from worker B, and metric names that mix in `orch_*`/KEDA/Mooncake series this lab
doesn't emit) with a small stdlib-only generator that builds rich, deterministic, per-worker
dashboards from metrics the two vLLM workers and DCGM actually expose today.

## Starting point

- `infra/inference/observability/grafana/dashboards/{vllm,cluster}.json` were committed, static
  copies of the class-9b example dashboards; their PromQL never used `sum by (...)` on a
  worker-distinguishing label, so worker A and B were indistinguishable in every panel.
- The class reference generator
  (`.vscode/myfiles/115-react-workload/experiments/class_dashboards_reference.py`) provided the
  `_target`/`_panel`/`_dash`/`write_dashboards` style to mirror, but most of its dashboards
  (`orch_*`, KEDA, HAMi allocator, Mooncake) reference metrics this lab does not emit.
- Real metric names were taken from `.vscode/myfiles/115-react-workload/experiments/{vllm-worker-b,dcgm}.prom`
  (vLLM v0.11.0 client, Qwen3-0.6B; DCGM exporter). Both files were copied (no IPs/hostnames found
  needing redaction) into `infra/inference/tests/fixtures/` as the contract test's allowlist source.
- `infra/inference/k8s/workers/{worker-a,worker-b}.yaml` and
  `infra/inference/observability/prometheus/values.yaml` were read to determine the
  worker-distinguishing label: Prometheus scrapes both workers as two `static_configs` targets
  (`inference-worker-a.inference-lab.svc.cluster.local:8000` / `-b...:8000`), so **`instance`** is
  the only label separating worker A from worker B in vLLM series (there is no per-pod relabeling
  configured). Kube-state/cAdvisor series are split by `pod` instead. `max_num_seqs=8`,
  `block_size=16` were read from the worker manifests rather than hardcoded blindly (though
  `block_size`/`num_gpu_blocks` are read live from the `cache_config_info` table panel, not baked
  into a query).

## Decisions

- Five dashboards instead of the class set's ten: KV & Prefix Cache, Prefill vs Decode, Scheduler
  & Concurrency, GPU & HAMi Slices, and a regenerated Cluster dashboard equivalent to the previous
  one. No gateway/router/orch/KEDA/Mooncake panels — those wait for #122/#133.
  `infra/inference/observability/grafana/dashboards.py` replaces the two static JSON files;
  `vllm.json` was removed since its content is now split across the KV/prefill-decode/scheduler
  dashboards.
- JSON is written with `sort_keys=True` for a stable diff and to make the "committed JSON equals
  generator output" contract test simple.
- Each dashboard has a text panel at the top with a short "how to read this" note tying the panel
  set back to the KV-cache/prefill-decode/queueing learning objective (e.g. slot saturation at
  `running / max_num_seqs = 1` vs KV-cache saturation).
- Added `make inference-dashboards` (documented with `##`) as the only supported way to
  regenerate; `infra/inference/scripts/validate.sh` already lints any dashboard JSON on disk, so
  no new validate step was needed.

## Verification

```bash
python3 infra/inference/observability/grafana/dashboards.py && git diff --exit-code   # clean after commit
uv run --project services/app pytest infra/inference/tests tests/inference -q          # 61 passed
bash infra/inference/scripts/validate.sh                                               # local-only, passes
```

Live rendering against a real Prometheus/Grafana instance is **unverified** — there is no GPU
instance running for this change. The contract test only checks that every metric name used
exists in the reference `.prom` captures (plus the kube/node/cAdvisor allowlist already used by
the prior `cluster.json`); it cannot verify label cardinality, Grafana schema acceptance, or that
the sidecar picks up the `inference-dashboards` ConfigMap correctly at deploy time.

## PR / merge state

Draft PR opened from `feat/115e-dashboards` in `.worktrees/115e-dashboards`; not merged. Built by
a Claude Sonnet 5 worker orchestrated by Opus 5.5.

## Lessons

- Histogram metrics (`_bucket`/`_sum`/`_count` suffixes) are not literally present as `# TYPE`
  lines in a `.prom` scrape — the contract test strips those suffixes before checking against the
  fixture-derived allowlist, otherwise every `histogram_quantile` panel would false-positive as
  using an "invented" metric.
- `static_configs` scraping (rather than Kubernetes pod service discovery) means the natural
  worker-splitting label is `instance`, not `pod`; this is easy to miss if you assume Prometheus
  always attaches a `pod` label.
