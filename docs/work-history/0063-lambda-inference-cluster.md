# Work history 0063 — Isolated Lambda inference cluster bundle

## Goal

Implement the self-contained, isolated Lambda inference lab bundle (`infra/inference/`) for issue #120,
bringing up two symmetric same-model vLLM workers, ClusterIP-only services, HAMi 50/50 GPU slicing,
Prometheus/Grafana/DCGM observability, warmup/capacity measurement tools, and safe lifecycle scripts.

## Starting point

- ADR 0010 was accepted in commit `3799347` (PR #130), defining the isolated lab boundary.
- Upstream class9b mechanics provided raw scripts, but contained unsafe exposure (`hostPort`, `hostNetwork`,
  `NodePort`), asymmetric models, floating unpinned versions, and future-scope elements (KEDA, gateway, Mooncake, UI).
- Issue #120 required two identical vLLM workers, fail-closed lifecycle scripts, and repeatable capacity measurements.

## Decisions

- **Strict Transfer Boundary**: Created `infra/inference/` as the single transferable subtree. The product FastAPI
  app, MCP service, React UI, and metrics stay local.
- **Symmetric Model & Workers**: Configured Worker A and Worker B identically with `Qwen/Qwen3-0.6B` at immutable
  revision `c1899de289a04d12100db370d81485cdf75e47ca`, `vllm/vllm-openai:v0.11.0`, bfloat16, 8192 context length,
  and 50% HAMi GPU memory/core limits.
- **ClusterIP-Only Isolation**: Prohibited `NodePort`, `hostPort`, and `hostNetwork: true`. Services are strictly
  `ClusterIP`. Remote `kubectl port-forward` binds loopback `127.0.0.1`.
- **Safe SSH Tunneling**: Exposed loopback-only local forwards for Worker A (18001), Worker B (18002), and
  Grafana (13000). Added a negative smoke check to verify ports are unreachable externally without the tunnel.
- **Pinned Dependencies**: Pinned k3s `v1.37.0+k3s1`, Helm `v3.22.0`, HAMi `2.9.0`, Prometheus `25.27.0`,
  and Grafana `8.5.1`.
- **Evidence and Measurement Tooling**: Built standard-library Python runners for KV bytes/token calculation,
  paper sequence ceilings (at taxi context lengths p50=512, p95=2048, max=8192), cold/warm TTFT aggregation,
  and deterministic run manifests under `metrics/inference/<run-id>/`.
- **Root Make Integration**: Exposed `inference-validate`, `inference-sync`, `inference-config`, `inference-secret`,
  `inference-bootstrap`, `inference-deploy`, `inference-up`, `inference-tunnel`, `inference-connect`,
  `inference-smoke`, `inference-warmup`, `inference-capacity`, `inference-run`, `inference-pull-evidence`,
  and `inference-teardown`.

## Files

- `infra/inference/README.md`
- `infra/inference/.env.example`
- `infra/inference/k8s/namespace.yaml`
- `infra/inference/k8s/hami/worker-slices.yaml`
- `infra/inference/k8s/hami/README.md`
- `infra/inference/k8s/workers/worker-a.yaml`
- `infra/inference/k8s/workers/worker-b.yaml`
- `infra/inference/k8s/services/worker-a.yaml`
- `infra/inference/k8s/services/worker-b.yaml`
- `infra/inference/observability/prometheus/values.yaml`
- `infra/inference/observability/prometheus/prometheus-values.yaml`
- `infra/inference/observability/grafana/values.yaml`
- `infra/inference/observability/grafana/grafana-values.yaml`
- `infra/inference/observability/dcgm/dcgm-exporter.yaml`
- `infra/inference/observability/dcgm/dcgm-values.yaml`
- `infra/inference/experiments/capacity.py`
- `infra/inference/experiments/warmup.py`
- `infra/inference/experiments/probe.py`
- `infra/inference/experiments/evidence.py`
- `infra/inference/scripts/validate.sh`
- `infra/inference/scripts/sync.sh`
- `infra/inference/scripts/config.sh`
- `infra/inference/scripts/secret.sh`
- `infra/inference/scripts/bootstrap.sh`
- `infra/inference/scripts/deploy.sh`
- `infra/inference/scripts/tunnel.sh`
- `infra/inference/scripts/smoke.sh`
- `infra/inference/scripts/pull-evidence.sh`
- `infra/inference/scripts/teardown.sh`
- `infra/inference/scripts/lib.sh`
- `infra/inference/tests/test_scripts_contract.py`
- `infra/inference/tests/test_manifests_contract.py`
- `tests/inference/test_cluster_bundle_contract.py`
- `tests/inference/test_capacity_evidence.py`
- `tests/inference/test_worker_probe.py`
- `Makefile`
- `docs/work-history/0063-lambda-inference-cluster.md`

## Verification

- `make inference-validate` — passed (all Kubernetes and observability YAML parse cleanly).
- `make --dry-run inference-up` and `make --dry-run inference-run` — passed.
- `source .venv/bin/activate && uv run --project services/app pytest tests infra/inference/tests` — all 67 tests passed.
- `uv run --project services/app ruff check infra/inference tests/inference` — clean, 0 errors.
- Script contracts verified: `--help` exits 0, missing env variables fail closed, secret values excluded from sync plan.
- Manifest contracts verified: 2 identical worker deployments, ClusterIP only, no hostPort/hostNetwork/NodePort, no `:latest`.

## PR and merge state

- Branch: `feat/120-inference-cluster`
- Worktree: `.worktrees/120-inference-cluster`
- Issue: #120
- Pull request: [PR #131](https://github.com/NakulManchanda/ai-analytics-poc/pull/131), Open draft
- State: Implementation and local verification complete; ready for draft PR and operator acceptance on Lambda when credentials are provided.

## Lessons

- Separating worker manifests (`worker-a.yaml`, `worker-b.yaml`) makes A/B symmetry instantly testable and diffable.
- Embedding safe defaults into `.env.example` allows complete local static and contract testing without requiring live credentials.
