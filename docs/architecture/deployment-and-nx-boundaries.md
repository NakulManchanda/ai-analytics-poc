# Deployable Units, Nx Project Graph, and Affected CI

This document records the repository's actual package, executable, container image, and environment deployment boundaries (building on the ownership refactor in epic #160) alongside the minimal Nx task runner adoption and affected-CI validation introduced in #252 (parent #251).

Every entry below is grounded in repository configuration (`docker-compose*.yml`, `infra/terraform/*.tf`, `infra/inference/k8s/gateway/gateway.yaml`, `scripts/deploy_frontend.sh`, `pyproject.toml`, `web/package.json`, `nx.json`, `*/project.json`, and `.github/workflows/ci.yml`).

---

## 1. Initial 5 Nx projects: packages, executables, images, and deployments

An **Nx project** is a build/test task unit. It does **not** map 1:1 to a Docker image or an AWS service — notably, `services/app` produces one image that runs two executables in Compose (`app` and `worker`) but only one service in AWS ECS, `web` uses Nginx in local Compose vs S3 + CloudFront in AWS, `evals` is local CLI/workbench tooling with no container image, and `gateway` runs on a generic Python image with a mounted ConfigMap on Lambda/k3s.

| Nx project | Directory & package | Executable / entrypoint | Docker image / artifact | Local Compose | AWS ECS / S3+CloudFront | Lambda / k3s inference lab |
| --- | --- | --- | --- | --- | --- | --- |
| `web` | `web/` (`ai-analytics-ui`, `web/package.json`, `web/package-lock.json`) | Static Vite SPA (`tsc --noEmit && vite build` -> `web/dist/`); local dev via `vite` | `web/Dockerfile` (Node 24 build -> `nginx:1.27-alpine` serving `/usr/share/nginx/html` + reverse-proxying `/api/*`) | `web` service in `docker-compose.yml` (port `${WEB_PORT:-3000}:8080`) | **Not a container in AWS.** Built and synced to private S3 bucket + CloudFront OAC via `scripts/deploy_frontend.sh` (`infra/terraform/s3.tf`, `cloudfront.tf`) | Not deployed |
| `app` | `services/app/` (`ai-app`, `services/app/pyproject.toml`, `services/app/uv.lock`) | 1) FastAPI API: `uvicorn app.main:app` (`:8080`)<br>2) Background job worker: `python -m app.worker` (see §2) | `services/app/Dockerfile` (`python:3.12-slim` + `uv==0.9.26`); ECR repo `${local.name}-ai-app` (`infra/terraform/ecr.tf`) | `app` and `worker` services in `docker-compose.yml`; overlaid by `aws`, `litellm`, `serve`, `phoenix`, `observability`, `jaeger`, `eval-capture` | `ai-app` ECS Fargate service behind ALB (`infra/terraform/ecs.tf`), `LLM_PROVIDER=litellm`, Service Connect to `analytics-mcp` and `litellm` (**no `worker` service**) | Not deployed on cluster; local Compose `docker-compose.serve.yml` points `app` + `worker` at `host.docker.internal:18080/serve` over SSH tunnel |
| `mcp` | `services/mcp/` (`analytics-mcp`, `services/mcp/pyproject.toml`, `services/mcp/uv.lock`; direct local dep on `../dataset_spike`) | FastMCP HTTP ASGI server: `uvicorn mcp_server.asgi:app` (`:8001`; local dev via `fastmcp run services/mcp/mcp_server/server.py`) | `services/mcp/Dockerfile` (repo-root build context copying `services/mcp`, `services/dataset_spike`, and `config/datasets`); ECR repo `${local.name}-analytics-mcp` | `mcp` service in `docker-compose.yml`; OTEL env overlaid by `phoenix` and `observability` | `analytics-mcp` ECS Fargate service (`infra/terraform/ecs.tf`), internal-only via Service Connect (`http://analytics-mcp:8001/mcp`) | Not deployed |
| `evals` | `tooling/evals/` (`ai-evals`, `tooling/evals/pyproject.toml`, `tooling/evals/uv.lock`) | CLI console script `evals` (`evals.cli:main`): `report`, `run-live`, `run-grade`, `run-compare`, `run-reconcile`, `run-trace-check`, `run-review`, `run-review-serve` (localhost HTTP review workbench) | None (host CLI / local Python package only) | Not a container; `make eval-stack-up` adds `docker-compose.eval-capture.yml` to mount `metrics/evals/_capture` into `app` | Not deployed | Not deployed |
| `gateway` | `services/gateway/` (no standalone `pyproject.toml`; linted/compiled/tested under `services/app` venv via `infra/inference/tests`) | FastAPI gateway: `uvicorn main:app --host 0.0.0.0 --port 8080 --app-dir /app` | None built by repo. Deployed in k3s using stock `python:3.12.9-slim`, runtime `pip install`, and `inference-gateway-code` ConfigMap (`infra/inference/k8s/gateway/gateway.yaml`) | Not in base Compose (local `docker-compose.serve.yml` reaches it via SSH tunnel on port `18080`) | Not deployed (ADR 0010 boundary) | `inference-gateway` Deployment in namespace `inference-lab` (`infra/inference/k8s/gateway/gateway.yaml`) |

---

## 2. `app.worker` environment usage audit

`services/app/app/worker.py` (`python -m app.worker`) is packaged inside the `services/app` image rather than its own container image or Nx project. Its actual usage across environments is narrow and asymmetric:

- **What it executes:** `JobWorker` consumes async background jobs from Redis Streams (`RedisJobConsumer`) for the `/api/jobs` path — currently only `job_type == "create_full_report"` — calling `mcp.get_dataset_profile()`, writing a JSON report artifact, updating durable state, and publishing `job.started` / `job.completed` / `job.failed` events.
- **What it does NOT execute:** Live conversational queries (`POST /api/ask`, `POST /api/runs`, token streaming, cancellation, and map queries `POST /api/map/zones`) execute inside the FastAPI `app.main:app` process, not in `app.worker`.
- **Local Docker Compose:**
  - `docker-compose.yml` defines a `worker` service (`build: ./services/app`, `command: ["uv", "run", "--no-sync", "python", "-m", "app.worker"]`, `LLM_PROVIDER: fake`). `scripts/smoke/12_local_integration.sh` starts and checks all five containers (`web`, `app`, `mcp`, `worker`, `redis`).
  - `docker-compose.serve.yml` overrides `worker` alongside `app` (`LLM_PROVIDER: serve`, `INFERENCE_GATEWAY_URL`).
  - `docker-compose.aws.yml` and `docker-compose.litellm.yml` override `app` only; `worker` stays on base `LLM_PROVIDER: fake` in those overlays.
- **AWS ECS (`infra/terraform/ecs.tf`):** **Not deployed.** Terraform defines task definitions and ECS services only for `ai-app`, `analytics-mcp`, and `litellm` (`infra/terraform/litellm.tf`). Comments in `infra/terraform/elasticache.tf` and `infra/terraform/outputs.tf` still mention `"app and worker tasks"`, which is a legacy description rather than a live ECS service.
- **Nx representation:** Modeled as a non-cached target `app:worker` (`"cache": false`) inside `services/app/project.json` rather than a separate Nx project, reflecting its shared package and image ownership.

---

## 3. Non-Nx / deferred packages and supporting runtimes

Per #251, Nx adoption starts with the 5 core projects above (`web`, `app`, `mcp`, `evals`, `gateway`). Benchmark tooling, internal library packages, notebooks, repo-level tests, and third-party/managed runtimes are inventoried separately below.

### Non-Nx / deferred repository packages & test suites

| Path | Package / ownership | Role | How it is verified today | Relationship to Nx |
| --- | --- | --- | --- | --- |
| `services/dataset_spike/` | `dataset-spike` (`pyproject.toml`, `uv.lock`) | DuckDB/Parquet NYC TLC query engine and `taxi_zones` aggregation logic | `make dataset-test` (`65 passed`), CI `python-services` (`pytest`, `black`, `ruff`, `compileall`) | Direct local dependency of `services/mcp`; declared as `datasetSpike` namedInput so edits invalidate `mcp`. Not a standalone Nx project yet. |
| `tooling/benchmarks/` | `ai-benchmarks` (`pyproject.toml`, `uv.lock`, optional `app` extra -> editable `services/app`) | Repeatable workload replayer (`benchmarks.run`) and request trace CLI (`benchmarks.trace_cli`) for inference & strategy studies | `make bench-test` (`133 passed`), CI `python-services` (`pytest`, `black`, `ruff`, `compileall`) | Deferred outside initial 5 Nx projects per #251; continues running in `make test` and CI `python-services`. |
| `tooling/experiments/` | `ai-analytics-poc-experiments` (`pyproject.toml`, `package = false`) | Local analysis script (`analysis.py`, stdlib-only) and Jupyter notebook (`build_notebook.py`, `123_evidence.ipynb`) | Stdlib unit tests in root `tests/experiments/` via `make infra-test` | Local analysis tooling; not an Nx project. |
| `tests/` (repo root) | No standalone package (runs under `services/app` venv) | Static assertions for Terraform (`tests/infra/`), scripts (`tests/scripts/`), inference bundle (`tests/inference/`), and analysis (`tests/experiments/`) | `make infra-test` (`98 passed, 1 skipped`), CI `python-services` (`pytest`, `black`, `ruff`) | Runs under `make test` and `python-services`; outside the initial 5 Nx projects. |

### Supporting runtimes (third-party containers, AWS managed services, GPU workers)

These runtimes are infrastructure or external services, not Nx projects:

- **Redis:** `redis:7-alpine` in `docker-compose.yml` (transient SSE streams, cancel flags, `/api/jobs` queue); AWS ElastiCache Redis (`infra/terraform/elasticache.tf`) in ECS.
- **LiteLLM proxy + Postgres:** `ghcr.io/berriai/litellm:v1.106.0` + `postgres:16-alpine` in `docker-compose.litellm.yml` (local Admin UI and spend logs); stateless ECS Fargate service (`infra/terraform/litellm.tf`) sharing `infra/litellm/config.yaml` and holding Bedrock IAM permissions.
- **Arize Phoenix:** `arizephoenix/phoenix:version-20.26.1` in `docker-compose.phoenix.yml` (local trace UI with `phoenix_data` volume; never deployed to AWS).
- **Local observability stack (`docker-compose.observability.yml` + opt-in `docker-compose.jaeger.yml`):** OpenTelemetry Collector `0.157.0`, Prometheus `v3.2.1`, Grafana `11.5.2`, Loki `3.4.2`, Grafana Alloy `v1.7.1`, Blackbox Exporter `v0.25.0`, and optional Jaeger `2.21.0`.
- **AWS managed services (`infra/terraform/`):** DynamoDB (`dynamodb.tf`), private S3 buckets for frontend and artifacts (`s3.tf`), CloudFront + OAC (`cloudfront.tf`), ALB (`alb.tf`), ECR (`ecr.tf`), Secrets Manager, CloudWatch Logs, Amazon Bedrock, and Amazon Transcribe.
- **Lambda/k3s GPU workers (`infra/inference/k8s/workers/worker-{a,b}.yaml`):** Two HAMi GPU-sliced `Qwen/Qwen3-0.6B` vLLM pods in namespace `inference-lab` (isolated from the AWS product deployment per ADR 0010).

---

## 4. Nx workspace design, inputs, and caching policy

- **Minimal pinning, zero package-manager migration:** Root `package.json` + `package-lock.json` pin `"nx": "24.0.6"` as the sole `devDependency` with **no** `workspaces` field. `web/package.json`, `web/package-lock.json`, and `web/Dockerfile` remain completely untouched.
- **Explicit configuration only:** `nx.json` sets `"useInferencePlugins": false` and `"analytics": false` so Nx executes only explicit `nx:run-commands` targets declared in the 5 `project.json` files without plugin inference surprises or interactive telemetry prompts. `.nxignore` excludes `.venv/`, `vev/`, `.worktrees/`, `.vscode/`, `metrics/`, `data/`, `**/__pycache__/`, `**/.venv/`, `web/dist/`, and `infra/terraform/.terraform/`.
- **No Makefile or workflow regression:** Every Nx target delegates to the exact existing `npm --prefix web` or `uv run --project ...` command. All existing `Makefile` targets remain unchanged; three small wrappers (`make nx-test`, `make nx-affected`, `make nx-graph`) were added.

### Cross-project input boundaries (`nx.json` `namedInputs` + project `inputs`)

| Named / explicit input | Files covered | Consuming Nx projects | Why |
| --- | --- | --- | --- |
| `sharedGlobals` | `nx.json` | All 5 (`web`, `app`, `mcp`, `evals`, `gateway`) | Changes to Nx cache/input rules invalidate all projects. |
| Root `package-lock.json` (Nx lockfile tracking) | `package-lock.json` | All 5 (`web`, `app`, `mcp`, `evals`, `gateway`) | Bumping the pinned Nx runner version intentionally invalidates all projects. |
| `pythonTooling` | `pyproject.toml` | `app`, `mcp`, `evals`, `gateway` (excludes `web`) | Root pytest and ruff settings apply to Python services only. |
| `datasetConfig` | `config/datasets/**/*` | `mcp` | MCP reads dataset metadata/pin config at runtime and builds it into its image. |
| `mapContract` | `config/datasets/nyc_yellow_taxi_2024_01.toml`, `web/src/map/taxi-zones.v1.json`, `web/src/map/fixtures/**/*`, `services/app/app/artifacts/taxi_zones_v1.json` | `app`, `web` | Shared `map_artifact` v1 geometry, allowlist, and golden fixtures (`services/app/tests/test_map_artifact.py`) without coupling unrelated `web/src/**` edits to `app`. |
| `captureContract` | `services/app/app/capture/**/*` | `app`, `evals` | `evals` parses and grades capture records produced by `app`. |
| `datasetSpike` | `services/dataset_spike/**/*` | `app`, `mcp` | `services/mcp` installs `services/dataset_spike` directly; `services/app/tests/test_query_catalogue.py` imports `dataset_spike.query_spec` and `test_map_router.py` inspects `query_spec.py`. |
| `mcpToolSchemas` | `services/mcp/mcp_server/**/*` | `mcp`, `app`, `evals` | `evals` verifies tool contracts and `services/app/tests/test_deep_agents_strategy.py` inspects `services/mcp/mcp_server/server.py`. |
| `litellmConfig` | `infra/litellm/config.yaml` | `app` | `services/app/tests/test_litellm_config_pricing.py` validates LiteLLM proxy pricing/model config. |
| `terraformConfig` | `infra/terraform/*.tf` | `app` | `services/app/tests/test_agent_strategy.py` and `test_litellm_config_pricing.py` assert invariants against `variables.tf` and `ecs.tf`. |
| `inferenceBundle` + `app` lockfile | `services/app/pyproject.toml`, `services/app/uv.lock`, `infra/inference/**/*`, `infra/observability/inference/**/*`, `Makefile` | `gateway` | `gateway` has no standalone `pyproject.toml` (runs under `services/app` venv), and `infra/inference/tests` validates `mooncake/`, `kv_transfer/`, `scripts/`, `k8s/`, `experiments/`, `infra/observability/inference/`, and `Makefile` targets. Using file inputs (instead of `implicitDependencies: ["app"]`) keeps unrelated `services/app/app/*` edits isolated from `gateway`. |

### Target caching policy

| Target(s) | Projects | Cache | Rationale |
| --- | --- | --- | --- |
| `test` | `web`, `app`, `mcp`, `evals`, `gateway` | `true` | Offline, fixture-backed unit/contract tests. |
| `lint` | `app`, `mcp`, `evals`, `gateway` | `true` | Deterministic `ruff check`. |
| `format-check` | `app`, `mcp`, `evals`, `gateway` | `true` | Deterministic `black --check`. |
| `compile` | `app`, `mcp`, `gateway` | `true` | Deterministic `python -m compileall -q`. |
| `build` | `web` | `true` (`outputs: ["{workspaceRoot}/web/dist"]`) | Deterministic `tsc --noEmit && vite build`. |
| `fixture-report` | `evals` | `true` | Deterministic `evals report` over committed fixtures. |
| `dev` | `web`, `app`, `mcp` | **`false`** | Long-running local dev servers. |
| `worker` | `app` | **`false`** | Long-running Redis job consumer. |
| `deploy` | `web` | **`false`** | Mutates AWS S3 + CloudFront (`scripts/deploy_frontend.sh`). |
| `run-live` | `evals` | **`false`** | Live networked/paid LLM eval runs writing under `metrics/evals/`. |
| `review-serve` | `evals` | **`false`** | Interactive local HTTP review server writing human verdicts. |

---

## 5. Affected-CI validation, measured timings, and keep-vs-fallback decision

`.github/workflows/ci.yml` now runs an additive `nx-affected` job in parallel with the existing `python-services` job (`fetch-depth: 0`, `NX_DAEMON=false`, `NX_TUI=false`). On `pull_request`, it diffs against `github.event.pull_request.base.sha`; on `push`, it diffs against `github.event.before`, and whenever the base SHA is empty, zeroed (`0000...`, e.g., a brand-new branch push), or unreachable, it falls back directly to `npx nx run-many` rather than `HEAD~1` so multi-commit pushes cannot silently skip earlier commits. `python-services` is untouched and remains the authoritative full-repo gate while `nx-affected` is validated on real PRs and `main` pushes.

### Empirical affected-scope proofs

Verified locally with `NX_DAEMON=false npx nx show projects --affected --files=<path>` (and confirmed with a temporary uncommitted git diff on `web/src/App.tsx` against `--base=HEAD`):

| Simulated edit | Affected projects reported by Nx | Expected? |
| --- | --- | --- |
| `web/src/App.tsx` | `web` | Yes — frontend-only change does not run Python tests/lints. |
| `web/src/map/fixtures/pickup-count-choropleth.v1.json` | `app`, `web` | Yes — shared map artifact fixture is tested by both `web` and `services/app/tests/test_map_artifact.py`. |
| `pyproject.toml` | `app`, `evals`, `gateway`, `mcp` | Yes — shared Python tooling fans out to all Python projects, skips `web`. |
| `config/datasets/nyc_yellow_taxi_2024_01.toml` | `app`, `mcp`, `web` | Yes — dataset/map config triggers all three consumers. |
| `services/app/app/capture/schema.py` | `app`, `evals` | Yes — capture contract fans out to `evals` without pulling in `gateway`. |
| `services/app/uv.lock` | `app`, `gateway` | Yes — shared `services/app` lockfile invalidates `gateway`. |
| `services/dataset_spike/dataset_spike/query_spec.py` | `app`, `mcp` | Yes — direct local dep of `mcp` and imported/read by `services/app/tests/test_query_catalogue.py` & `test_map_router.py`. |
| `services/mcp/mcp_server/server.py` | `app`, `evals`, `mcp` | Yes — MCP tool surface invalidates `mcp`, `app`, and `evals`. |
| `infra/litellm/config.yaml` | `app` | Yes — validated by `services/app/tests/test_litellm_config_pricing.py`. |
| `infra/terraform/ecs.tf` | `app` | Yes — validated by `services/app/tests/test_agent_strategy.py`. |
| `infra/inference/scripts/deploy.sh` | `gateway` | Yes — inference bundle scripts are covered by `infra/inference/tests`. |
| `infra/observability/inference/grafana/dashboards.py` | `gateway` | Yes — inference Grafana/Prometheus contracts are covered by `infra/inference/tests`. |
| `Makefile` | `gateway` | Yes — `test_scripts_contract.py` and `test_remote_dir_contract.py` assert inference Make targets. |
| `package-lock.json` | `app`, `evals`, `gateway`, `mcp`, `web` | Yes — bumping root Nx runner dependencies intentionally marks all 5 projects affected. |

### Measured local wall times

Measured on Apple Silicon (`node v24.10.0`, `uv 0.9.26`, `nx 24.0.6`):

| Workload | Scope | Cache | Measured wall time |
| --- | --- | --- | --- |
| `make test` (sequential baseline) | `app` (701+1s), `benchmarks` (133), `gateway` (396), `mcp` (59), `dataset_spike` (65), root `tests/` (98+1s), `evals` (205+2s), `web` test (123) + build | None | **77.49s** |
| Cold `nx run-many` (`rm -rf .nx/cache`) | 18 tasks across 5 Nx projects (`test`, `lint`, `format-check`, `compile`, `build`, `fixture-report`) in parallel | 0/18 | **73.64s** |
| Warm `make nx-test` | Same 18 tasks across 5 Nx projects | **18/18 hit (100%)** | **0.99s** (11ms Nx runner) |
| Cold `web`-only `nx affected` (`--skip-nx-cache`) | 2 tasks (`web:test`, `web:build`) | Skipped | **5.94s** (5.3s Nx runner) |

### Coverage comparison: `nx-affected` vs `python-services`

`nx-affected` intentionally covers the initial 5 Nx projects (`web`, `app`, `mcp`, `evals`, `gateway`) and adds `evals:fixture-report`, but does **not** yet replace `python-services` because `python-services` additionally runs:

1. `tooling/benchmarks` (`bench-test` [133 tests], `black`, `ruff`, `compileall`),
2. `services/dataset_spike` (`dataset-test` [65 tests], `black`, `ruff`, `compileall`),
3. Root `tests/` (`infra-test` [98 passed, 1 skipped], `black`, `ruff`),
4. `make compose-smoke` (multi-container Docker Compose UI smoke).

Because `nx-affected` currently installs all three Python virtualenvs (`services/app`, `services/mcp`, `tooling/evals`) and `web` dependencies up front on cold runners before filtering tasks, its CI speedup is concentrated in **task execution time** (e.g., ~5.9s vs ~73.6s on a `web`-only change, a >12x task reduction) rather than runner bootstrap time.

### Keep-vs-fallback decision rule (Nx vs `Makefile` + GitHub Actions path filtering)

Note on scope: `services/dataset_spike` standalone tests, `tooling/benchmarks`, and root `tests/` intentionally remain outside the initial 5 Nx projects (covered by `python-services` CI and `make test`). Any future decision to replace `python-services` with Nx requires either bringing those suites into Nx or keeping dedicated CI jobs for them.

Keep Nx as the repo's task runner if, after 2–4 weeks of real PRs:
- `nx-affected` stays green with zero false-negative skips on cross-project contract edits,
- Local developers benefit from the sub-second warm cache (`0.99s` vs `77.49s`) and parallel task execution, and
- Expanding Nx to the remaining Python packages (`dataset_spike`, `benchmarks`, root `tests`) or per-job dependency install gating in CI looks clean without introducing Nx Cloud or JS workspace complexity.

Fall back to **`Makefile` + GitHub Actions path filtering** (e.g., `dorny/paths-filter` gating existing `make` targets) if:
- Maintaining root `package.json`/`package-lock.json` and `project.json` input globs creates recurring drift or false-negative CI misses, or
- Runner bootstrap overhead (`npm ci` + `uv sync`) dominates wall time so much that `nx-affected` delivers <20% net CI wall-time savings over simple job-level Actions path filters without remote caching.
