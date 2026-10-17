# Work 0093 — Consolidate experiments and observability layout

## Milestone

Repository structural refactor, epic #160, issue #163 (3 of 3)

## Branch

`refactor/163-experiments-observability`

## Worktree

`.worktrees/163-experiments-observability`

## Goal

Make ownership of experiment analysis and observability configuration discoverable with no behavior change.

## Starting state

Root `experiments/` (analysis package, notebook, plots), `infra/inference/experiments/` (cluster runners and manifests), root `observability/` (local Compose stack) and `infra/inference/observability/` (cluster Prometheus/Grafana/DCGM) lived in unrelated places.

## Decisions

- `observability/` -> `infra/observability/core/`; `infra/inference/observability/` -> `infra/observability/inference/`. The cluster configs are still deployed by rsync, so the remote layout (`$RDIR/observability`) is unchanged; `sync.sh` excludes `/observability` from the infra rsync and copies `infra/observability/inference/` to it (`OBSERVABILITY_ROOT` in `lib.sh`), the same pattern as the gateway in work 0092.
- `experiments/` -> `tooling/experiments/`; import path `experiments.analysis` -> `tooling.experiments.analysis`; notebook regenerated (only import lines changed).
- `infra/inference/experiments/` (capacity, warmup, probe, evidence runners and manifest env) stays: it is deployment-specific and runs against the remote host.
- Service-local tests (`tests/experiments`, `infra/inference/tests`, `tests/scripts`) did not move; only paths were fixed.
- Grafana/Prometheus configuration is kept separate from application tracing code (`services/app/app/telemetry.py`, untouched).
- README gained a folder-ownership table and a Mermaid diagram of the full-app, direct-gateway and Redis job paths (pushed to `main` ahead of the PR).

## Verification

- `tests` + `infra/inference/tests`: 489 passed, 1 skipped before and after; `services/app/tests`: 220; `make bench-test`: 133; `tests/experiments` under the experiments project: 30 passed.
- `docker compose -f docker-compose.yml -f docker-compose.observability.yml config -q` ok; `bash -n` on scripts; `sync.sh --plan` lists both sources; regenerated Grafana dashboards produce no diff; black/ruff clean in CI scope.

## Limitations

- `make observability-smoke` not run (Docker unavailable); the Compose config resolves the moved mounts.
- Remote rsync/deploy not run against a real host (same deferral as work 0092).
- `plot_evidence.py` only had its usage text updated, not executed.
- Historical work-history entries and ADRs still mention old paths.

## Pull request

See the PR for #163.
