# Work 0092 — Move gateway to services/gateway

## Milestone

Repository structural refactor, epic #160, issue #162 (2 of 3)

## Branch

`refactor/162-gateway-service` (stacked on `refactor/161-benchmark-tooling`, PR #164)

## Worktree

`.worktrees/162-gateway-service`

## Goal

Separate the deployable gateway FastAPI code from GPU/K8s infrastructure: `infra/inference/gateway/` -> `services/gateway/`, with no behavior change.

## Starting state

Gateway code sat under `infra/inference/`. It is deployed as flat files via the `inference-gateway-code` ConfigMap built from a remote `gateway/` directory; tests imported `infra.inference.gateway`.

## Decisions

- `git mv` of all 12 files; tests and callers import `services.gateway` (namespace package under the root pytest `pythonpath`).
- Packaging: keep the remote layout (`$RDIR/gateway`), the ConfigMap name and `uvicorn main:app --app-dir /app` unchanged. `sync.sh` and `gateway-restart.sh` now rsync from `services/gateway/` (new `GATEWAY_ROOT` in `lib.sh`); `sync.sh` excludes `/gateway` from the infra rsync so its `--delete` cannot remove it.
- Flat-import fallbacks kept: deployment compatibility cannot be proven without a cluster.
- Contract tests extended to assert the new source location and the unchanged remote path/ConfigMap/entrypoint.

## Verification

- `infra/inference/tests` + `tests/inference`: 431 passed before and after; `tests/`: 93 passed, 1 skipped (nbclient) before and after.
- `bash -n` on edited scripts; `sync.sh --plan` shows the new source; `cd services/gateway && python -c "import main"` loads as a flat module.

## Limitations

- The rsync/ConfigMap flow and `--exclude '/gateway'` were not run against a real host or cluster; needs a live `make inference-sync`/`inference-gateway-restart` check by the owner.
- `infra/inference/tests` is still not black-clean (32 files) and stays outside the CI lint scope; `services/gateway` is now black-formatted (separate formatting-only commit) and covered by CI black/ruff/compileall, and `make test` now runs `gateway-test` (`infra/inference/tests`).
- Historical work-history entries still mention old paths.

## Pull request

PR #165 (closes #162), rebased onto `main` after #164 merged.
