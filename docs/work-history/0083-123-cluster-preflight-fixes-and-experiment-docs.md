# Work history 0083 — #123 cluster pre-flight and E1: deploy/script fixes, worker startupProbe, taxi schema pin, manifest inputs, experiment docs folder

## Goal

Get the first real #123 cluster session (Lambda A100, k3s) through deploy and pre-flight so E1-E5 can be run by hand, and keep the fixes found along the way. Operator ran every cluster command; changes were made as uncommitted edits in the main checkout.

## Starting point

- `make inference-deploy` had never completed against a live cluster: the Prometheus helm install failed twice, and the playbook/runbook had never been executed (written without cluster access).
- Docs for the session lived partly in gitignored scratch (`.vscode/myfiles/...`), and the three inference experiment docs sat flat in `docs/`.

## Decisions

- **Worker startupProbe.** A worker restart took 94-104 s while its liveness probe killed the container at about 120 s (no `startupProbe`); one slow start (torch.compile cache miss) put worker A in a restart loop for about 6 minutes. Both worker manifests now have a `startupProbe` (`/health`, period 10 s, failureThreshold 30), and `restart-test.sh` waits 300 s instead of 120 s for the rollout.
- **E1 harness.** `make inference-e1` (`run-e1.sh`) runs E1 end to end: load the E1 manifest, confirm, restart B then A, wait for the tunnel port-forwards to re-attach (the first attempt's cold run hit a dead forward and wrote `{}`), cold run (aborts if empty), wait `WARM_WAIT_S` (default 300), warm run, Prometheus range pull over the run window, evidence pull. `make inference-verify-workers` (read-only) compares worker image, args and startup-log engine settings (including chunked prefill and prefix caching) with the manifest. `make inference-tmux-start|end` run a tmux session (run, tunnel, watch windows) and `end` also kills leftover `kubectl port-forward` loops on the instance. `make inference-fresh-up` chains up + controls on + verify for a new instance (not run).
- **Run ids** are now `d123-<date>-<exp>-<HHMM>` (local time, set by the manifest env files) so reruns never overwrite; source an experiment file once per experiment so both arms share a folder.
- **Dashboard.** vLLM's request time histograms start at 0.3 s (ITL at 10 ms), so the Prefill/Decode/Queue/Inference/ITL quantile panels show interpolated artifacts (p50 150 ms, p95 285 ms) for fast requests. Those five panels gain a `mean` series (`rate(_sum)/rate(_count)`) and the note says so.

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
- E1 (live, operator-run): `make inference-e1` completed all six steps; restarts took 94 s (B) and 95 s (A), both smoke checks passed, no probe-kill loop; startupProbe confirmed live on both deployments. Engine-side TTFT from the vLLM histograms: after the restart 5 of 6 requests per worker were 5-10 ms and 1 was 10-20 ms (same on both workers); after a 300 s idle gap all 6 per worker were 5-10 ms. Client-side TTFT through the SSH tunnel (250-350 ms, jitter up to 579 ms) could not resolve this. One cold request per worker per run; the slower request being the first is inferred.
- Local at commit time: `uv run --project services/app pytest infra/inference/tests services/app/tests/test_scenarios.py tests/experiments -q` — 225 passed; `bash -n` on the new and changed scripts; secret/IP/absolute-path scan of the changed files found nothing.
- Not verified: `make inference-controls-off`, the rewritten `gateway-restart.sh` against a live cluster, `tmux-session.sh`, `inference-fresh-up`, the startupProbe under a slow start, the new dashboard `mean` series rendering, the full service test suites and CI, and `promtool`. E0 (capacity, worker A only) and E1 were run; E0 through the gateway and E2-E5 were not.

## PR / merge state

No PR. Committed directly to `main` by request (larger than the direct-to-main exception in AGENTS.md); not pushed at the time of writing. Nothing here deploys or applies infrastructure by itself; the probe change takes effect only on `make inference-deploy`, which the operator ran.

## Open items

- Gateway and workers are scraped twice (pod annotations plus static jobs in `prometheus/values.yaml`), so unfiltered sums double-count and dashboard legends repeat. Confirmed for the gateway on a live `worker_warm` query; the worker duplicate is inferred. Workaround: filter by `job`. Fix parked.
- Tenant quota (`other` bucket, default max concurrency 4) may return 429 during the E0 sweep above 4 concurrent requests, which would put the knee at the quota rather than the GPU. Inferred from `tenants.py`; not yet observed.
- Fixed in this change: `infra/inference/README.md` (alerts are loaded through a temp values file, not `--set-file`) and the testing guide's nonexistent `make inference-status`. Left as historical record: `docs/work-history/0078` lines 18 and 33 still say `--set-file`.
- E0 through the gateway (sweep), E2, E3, E4 and the fresh-instance rehearsal are still to do.

## Lessons

- Quoting and `~` expansion across SSH is a recurring failure source; keep paths in a remote-side variable and quote them.
- A venv holding a non-editable copy of a local package goes stale silently; refresh with `uv sync --reinstall-package`.
- Inactive alerts prove the rules loaded, not that they can fire; check the metric names return data once traffic flows.
