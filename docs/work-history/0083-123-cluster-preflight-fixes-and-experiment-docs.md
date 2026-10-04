# Work history 0083 — #123 cluster pre-flight: deploy/script fixes, taxi schema pin, manifest inputs, experiment docs folder

## Goal

Get the first real #123 cluster session (Lambda A100, k3s) through deploy and pre-flight so E1-E5 can be run by hand, and keep the fixes found along the way. Operator ran every cluster command; changes were made as uncommitted edits in the main checkout.

## Starting point

- `make inference-deploy` had never completed against a live cluster: the Prometheus helm install failed twice, and the playbook/runbook had never been executed (written without cluster access).
- Docs for the session lived partly in gitignored scratch (`.vscode/myfiles/...`), and the three inference experiment docs sat flat in `docs/`.

## Decisions

- `deploy.sh`: alert rules are now nested under `serverFiles.alerting_rules.yml` in a temp values file passed with `-f` (chart 25.27.0 rejects `--set-file` for that key because it expects an object, not a string). `--from-file=` for the dashboards and gateway-code ConfigMaps uses a remote-side `$RDIR` because bash does not expand `~` after `=`; `INFERENCE_REMOTE_DIR` is quoted in `.env.example` for the same reason.
- `gateway-restart.sh`: syncs only `infra/inference/gateway/` instead of the whole tree (the full `rsync --delete` could remove remote-only files such as `evidence/`), and has the same `~` fix.
- New `infra/inference/scripts/controls.sh` with `make inference-controls-on|off` replaces a hand-typed `kubectl set env` over SSH (sets `ALLOW_EXPERIMENT_CONTROLS` and the E4 `TENANT_ALLOWLIST`; off also removes `ALLOW_FORCED_PLACEMENT`).
- Taxi schema pin: the live MCP schema has `store_and_fwd_flag`; the pinned copy had `store_and_forward_flag`. Fixed in `canonical_prefix.py` and in the baked-in `system_prefix` of `config/scenarios/e3_routing_mixed.json` (the drift test requires them to match).
- Manifest inputs for the replayer are now files: `infra/inference/experiments/manifest/common.env` (shared, non-secret) plus `e0..e5` files that source it and set `RUN_ID`. Kept out of `.env` because the replayer does not read `.env` and sourcing it would also export `HF_TOKEN`. Tokenizer and chat-template revisions are set equal to the model revision as an explicit assumption (no `--tokenizer` flag is set).
- Docs: the session runbook is committed as `docs/inference-experiments/inference-session-runbook.md`, and the playbook, testing guide and evidence index moved into `docs/inference-experiments/` with references updated (Makefile comment, `experiments/` docstrings and notebook, project plan, intra-folder link). Historical work-history entries 0080/0081 still cite the old paths on purpose.
- Two new docs in the same folder: `docs/inference-experiments/README.md` (how Prometheus, Grafana, alerts and the scrape jobs are deployed, when `make inference-deploy` applies them, and how observations are captured, plus an index of the folder) and `inference-experiments-reference.md` (a quick E0-E5 reference that notes where the testing guide's E0/E2 definitions differ from the runbook and playbook).

## Verification

- Live (operator-run): `make inference-deploy` completed after the fixes (all rollouts succeeded); all 8 pods Running, 0 restarts; both workers `/health` 200; gateway `/health` ok; `worker_warm` = 1 for both workers; Prometheus `/alerts` lists the 5 rules (all Inactive); `/tokenize` returned `count: 1`, `max_model_len: 8192`; `make inference-controls-on` rolled out and health/`worker_warm` were rechecked afterwards.
- Local: `uv run --project services/app pytest services/app/tests/test_scenarios.py -q` — 10 passed (failed first on the stale JSON prefix, passed after the fix); `uv run --project services/app pytest tests/experiments -q` — 29 passed after the doc moves; `bash -n` on `controls.sh` and `gateway-restart.sh`; `make -n inference-controls-on`; `common.env`/`e3-routing.env` sourced in bash and zsh.
- Not verified: `make inference-controls-off`, the rewritten `gateway-restart.sh` against a live cluster, the full service test suites and CI, `promtool`, live rendering of most Grafana panels (many need traffic), and any experiment run. No E1-E5 evidence was captured in this change.

## PR / merge state

No PR yet. All changes are uncommitted in the main checkout by request; the operator will push them together later. Nothing here deploys or applies infrastructure.

## Open items

- Gateway and workers are scraped twice (pod annotations plus static jobs in `prometheus/values.yaml`), so unfiltered sums double-count and dashboard legends repeat. Confirmed for the gateway on a live `worker_warm` query; the worker duplicate is inferred. Workaround: filter by `job`. Fix parked.
- Tenant quota (`other` bucket, default max concurrency 4) may return 429 during the E0 sweep above 4 concurrent requests, which would put the knee at the quota rather than the GPU. Inferred from `tenants.py`; not yet observed.
- Stale docs still to fix: `infra/inference/README.md:206` (`--set-file`), `docs/work-history/0078` lines 18 and 33, `docs/inference-experiments/inference-testing-guide.md:54` (`make inference-status` does not exist).

## Lessons

- Quoting and `~` expansion across SSH is a recurring failure source; keep paths in a remote-side variable and quote them.
- A venv holding a non-editable copy of a local package goes stale silently; refresh with `uv sync --reinstall-package`.
- Inactive alerts prove the rules loaded, not that they can fire; check the metric names return data once traffic flows.
