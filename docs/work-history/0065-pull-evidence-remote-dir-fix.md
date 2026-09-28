# Work history 0065 — Fix quoted-tilde remote-dir bug in evidence pull scripts

## Goal

Fix `make inference-pull-evidence` failing with rsync "No such file or directory" (Error 23),
and stop a plain cluster-snapshot pull from failing just because it lacks capacity-run artifacts.

## Starting point

- `infra/inference/scripts/lib.sh`'s `remote_dir()` defaults to `~/ai-analytics-inference`.
  Because `$(remote_dir)` is expanded locally (via command substitution) before the resulting
  text is spliced into a heredoc sent over SSH, any script that then wrapped that spliced text
  in single or double quotes on the remote side prevented the remote shell from tilde-expanding
  it. `pull-evidence.sh`'s capture step (`EVID_DIR="$(remote_dir)/evidence/$RUN_ID"`) hit this,
  creating a literal `./~/ai-analytics-inference/evidence/<run>` directory on the remote host,
  while the later `rsync "$(ssh_target):$(remote_dir)/evidence/$RUN_ID/"` relies on rsync's own
  (unquoted) tilde handling and looked for the real, tilde-expanded home path — so it failed.
- Separately, `pull-evidence.sh` always ran `experiments/evidence.py` after a successful pull,
  which raises `ValueError` when `raw/responses.jsonl` (or its fallbacks) is missing — so a
  cluster-snapshot-only pull (no capacity run) always exited non-zero even though the pull itself
  succeeded.

## Decisions

- Removed the remote-side double quotes around `EVID_DIR`'s assignment in `pull-evidence.sh` so
  the spliced `~/ai-analytics-inference/...` text tilde-expands on the remote host, matching how
  rsync already resolves the same expression on the pull side.
- Swept every other script under `infra/inference/scripts/` for the same class of bug (any
  `$(remote_dir)`-derived path wrapped in quotes before being sent to the remote shell) and fixed
  it the same way:
  - `deploy.sh`: unquoted all `$(remote_dir)/...` paths passed to `kubectl apply`/`helm -f`/
    `--from-file=` and the `[[ -d ... ]]` checks.
  - `gateway-restart.sh`: unquoted the `--from-file=$(remote_dir)/gateway` path.
  - `sync.sh`: unquoted the remote `mkdir -p $TARGET` and simplified the rsync destination to
    `"$(ssh_target):$TARGET"` (previously used inner escaped quotes) so the mkdir'd path and the
    rsync'd path are built from the exact same `$TARGET` expression.
  - `restart-test.sh`: unquoted the remote `ls -td $REMOTE_BASE/evidence/*` glob.
- Left `remote_dir()`'s default (`~/ai-analytics-inference`) unchanged, since rsync's own
  remote-tilde handling depends on an unquoted, literal `~` — switching to `$HOME` would have
  broken rsync's path resolution instead (rsync does not expand arbitrary `$VAR` in remote paths,
  only a leading `~`).
- `pull-evidence.sh` now checks for `raw/responses.jsonl`, `raw/capacity_responses.jsonl`, or
  `raw/smoke_responses.jsonl` under the pulled run directory before invoking
  `experiments/evidence.py`; if none exist, it prints a clear skip message and exits 0 instead of
  propagating `evidence.py`'s `ValueError`. The strict `make inference-run` path (smoke -> warmup
  -> capacity -> pull, followed by the Makefile's own unconditional `evidence.py` invocation)
  is unchanged and still requires capacity artifacts.
- Did not add Prometheus TSDB history export — out of scope per explicit direction; that belongs
  to a later issue (#123) since the Lambda host and its Prometheus history are treated as
  disposable.

## Files

- `infra/inference/scripts/lib.sh` (read only, no change — confirmed `remote_dir()`'s contract)
- `infra/inference/scripts/pull-evidence.sh`
- `infra/inference/scripts/deploy.sh`
- `infra/inference/scripts/gateway-restart.sh`
- `infra/inference/scripts/sync.sh`
- `infra/inference/scripts/restart-test.sh`
- `infra/inference/README.md`
- `infra/inference/tests/test_remote_dir_contract.py` (new)
- `docs/work-history/0065-pull-evidence-remote-dir-fix.md`

## Verification

- `bash -n` on every changed script — all pass.
- `shellcheck` was not available in this environment (`which shellcheck` reported not found); not run.
- `uv run --project services/app python -m pytest infra/inference/tests tests/inference -q` —
  53 passed, including the new `test_remote_dir_contract.py` asserting no script wraps a
  `$(remote_dir)`-derived path in quotes before remote use, that `pull-evidence.sh`'s capture and
  rsync paths share the same `$(remote_dir)/evidence/$RUN_ID` expression, and that the summarizer
  skip behavior is present.

## Limitations / not verified

- Could not exercise the fix against a real Lambda host or run any `make inference-*` target
  that uses SSH/rsync against a live remote (explicitly out of scope for this change per
  instructions) — verification is limited to shell syntax checks and the new/existing pytest
  contract tests reasoning about the script text itself.
- `shellcheck` was unavailable locally, so only `bash -n` syntax checking was performed.

## PR / merge state

- Branch: `fix/pull-evidence-remote-dir`, worktree: `.worktrees/fix-pull-evidence-tilde`.
- Opened as a draft PR; not merged.

## Lessons

- `$(cmd)` inside a string that will itself be sent to a remote shell via SSH is expanded
  *locally*, not remotely — so any local text containing an unexpanded `~` needs to reach the
  remote shell *unquoted* to actually tilde-expand there. Tools like rsync that do their own
  remote-tilde resolution can silently succeed while a hand-rolled `ssh_cmd "... '$(...)' ..."`
  next to it silently fails on the exact same input, which is what made this bug hard to notice
  until Error 23 line-fired.
