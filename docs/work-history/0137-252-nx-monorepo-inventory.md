# 0137 — Deployable-unit inventory, minimal Nx adoption, and affected CI (#252, parent #251)

## Goal
Document the repository's real package, executable, Docker image, and environment deployment boundaries (linked to #160), adopt pinned Nx incrementally as a local task runner for the initial 5 projects (`web`, `app`, `mcp`, `evals`, `gateway`) with explicit cross-project inputs and local caching, and validate an additive `nx-affected` GitHub Actions CI job alongside existing CI.

## Starting point
`make test` and `.github/workflows/ci.yml` (`python-services`) ran all Python and React checks unconditionally on every non-Markdown change with no task graph or local cache. Package/image/deployment boundaries (notably `app.worker` present in Compose but absent in ECS, `web` served via Nginx in Compose vs S3+CloudFront in AWS, and `services/gateway` running via ConfigMap on Lambda/k3s) were spread across Compose files, Terraform configs, K8s YAML, and `Makefile`.

## Decisions
- **Minimal root Nx pin without package-manager migration:** Added root `package.json` + `package-lock.json` pinning `"nx": "24.0.6"` in `devDependencies` with no `workspaces` field. Left `web/package.json`, `web/package-lock.json`, and `web/Dockerfile` completely untouched.
- **Explicit targets only, zero inference plugins:** Configured `nx.json` with `"useInferencePlugins": false` and `"analytics": false`, plus `.nxignore` excluding `.venv/`, `vev/`, `.worktrees/`, `.vscode/`, `metrics/`, `data/`, `**/__pycache__/`, `**/.venv/`, `web/dist/`, and `infra/terraform/.terraform/`. Updated `.gitignore` for `.nx/` and `/node_modules/`.
- **Truthful cross-project inputs & strict cache boundaries:** Defined `project.json` for `web`, `app`, `mcp`, `evals`, and `gateway` wrapping existing `npm --prefix web` and `uv run --project ...` commands via `nx:run-commands`. Used targeted `namedInputs` (`pythonTooling`, `datasetConfig`, `captureContract`, `mapContract`, `datasetSpike`, `mcpToolSchemas`) and file-level `services/app/uv.lock` inputs on `gateway` (instead of broad `implicitDependencies: ["app"]`) so `web`-only edits affect only `web` and `services/app/app/capture/schema.py` affects `app` + `evals` without falsely triggering `gateway`. Marked all live, stateful, dev, and deploy targets (`web:dev`, `web:deploy`, `app:dev`, `app:worker`, `mcp:dev`, `evals:run-live`, `evals:review-serve`) with `"cache": false`.
- **Additive CI + Make ergonomics:** Added a parallel `nx-affected` job to `.github/workflows/ci.yml` (`fetch-depth: 0`, `NX_DAEMON=false`, `NX_TUI=false`, robust `pull_request` and `push` base-SHA handling with fallback) without removing or weakening `python-services`. Added `make nx-test`, `make nx-affected`, and `make nx-graph` while keeping all existing Make targets intact.
- **Grounded boundary inventory:** Authored `docs/architecture/deployment-and-nx-boundaries.md` linked from `README.md`, including an explicit `app.worker` Compose-vs-ECS audit, separate inventory of deferred packages (`tooling/benchmarks`, `services/dataset_spike`, `tooling/experiments`, root `tests/`) and supporting runtimes, measured timings, coverage gaps, and a clear keep-vs-fallback rule vs `Makefile` + Actions path filtering.

## Verification
- `NX_DAEMON=false npx nx show projects`: exact 5 projects (`gateway`, `evals`, `app`, `mcp`, `web`).
- Affected-scope proofs (`nx show projects --affected --files=...` + temporary real git-diff check against `HEAD`):
  - `web/src/App.tsx` -> `web`
  - `pyproject.toml` -> `app`, `evals`, `gateway`, `mcp`
  - `config/datasets/nyc_yellow_taxi_2024_01.toml` -> `app`, `mcp`, `web`
  - `services/app/app/capture/schema.py` -> `app`, `evals`
  - `services/app/uv.lock` -> `app`, `gateway`
  - `services/dataset_spike/dataset_spike/analytics.py` -> `mcp`
  - `services/mcp/mcp_server/server.py` -> `mcp`, `app`, `evals`
- Local caching & wall times:
  - Cold `nx run-many -t test,lint,format-check,compile,build,fixture-report`: 18/18 tasks passed in `73.64s`.
  - Warm `make nx-test`: 18/18 tasks hit local cache (`100%`, `11ms` Nx runner, `0.99s` wall time).
  - Cold `web`-only `nx affected` (`--skip-nx-cache`): 2/2 tasks (`web:test`, `web:build`) passed in `5.94s`.
  - Non-deterministic targets (`web:dev`, `web:deploy`, `app:dev`, `app:worker`, `mcp:dev`, `evals:run-live`, `evals:review-serve`) verified `"cache": false` via `nx show project <name> --json`.
- Full `make test`: passed 100% in `77.49s` (`services/app`: 701 passed, 1 skipped; `benchmarks`: 133 passed; `gateway`: 396 passed; `mcp`: 59 passed; `dataset_spike`: 65 passed; root `tests`: 98 passed, 1 skipped; `evals`: 205 passed, 2 skipped; `web`: 123 passed + `vite build`).
- `.github/workflows/ci.yml` validated with Python `yaml.safe_load`; diff scanned for secrets and developer-specific absolute paths.

## Limitations / lessons
- Nx 24 prompts interactively for usage analytics unless `"analytics": false` is set in `nx.json`; setting it keeps local scripts and CI non-interactive.
- `nx-affected` currently covers the 5 initial Nx projects; `tooling/benchmarks`, `services/dataset_spike` standalone tests, root `tests/`, and `make compose-smoke` remain covered by `python-services` until the team evaluates keeping vs expanding Nx.
- In CI without remote caching, `nx-affected` still pays `npm ci` + `uv sync` bootstrap time across runners, so net CI savings should be evaluated against `Makefile` + Actions path filtering after observing live PR runs.

## PR / merge state
Draft PR on branch `feat/252-nx-monorepo-inventory` (worktree `.worktrees/252-nx-monorepo-inventory`), targeting #252 (parent #251).
