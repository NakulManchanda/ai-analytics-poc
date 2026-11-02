---
name: dev-commands
description: Use when the user asks how to run the app, run an eval, run the reviewer, stop things, check traces or cost, or asks "what commands do I need". Cheat sheet of the common Make targets and worktree/PR commands for this repo.
---

# Common commands

The root `Makefile` is the operator interface; `make help` lists every target. Prefer a target over a hand-built command. Live Bedrock calls cost money: the user runs them (give the commands, inspect files afterwards). Never stop another task's Compose project; only stop one you started or the user names.

## Run the app (real AWS, Bedrock via LiteLLM proxy, Phoenix traces)
- `make local-aws-litellm-compose` — start the stack (needs Docker and AWS credentials; `AWS_PROFILE=<profile>`, `WEB_PORT=3000`, `LITELLM_PORT=14000`, `LITELLM_CAPTURE=1` stores prompts/responses, `LITELLM_OTEL=1` routes traces through the Collector (+Prometheus/Grafana), `JAEGER=1` adds Jaeger).
- `make local-aws-refresh` — down, then rebuild and start from scratch.
- `docker compose -p <project> down` — stop a stack (project defaults to the checkout directory name; list with `docker ps`). Volumes are kept.
- `make dev` / `make mcp-dev` — run the app (port 8080) or MCP service (8001) without Docker.
- `make litellm-ready` — proxy health. `make bedrock-smoke` / `make litellm-smoke` — one bounded paid call.

## Look at what happened
- `make phoenix-ui` (trace UI URL), `make litellm-ui`, `make litellm-calls [SESSION=conv_...] [LIMIT=20]`, `make litellm-spend`, `make litellm-errors`.
- `make phoenix-down` stops only Phoenix; `make phoenix-purge` deletes all stored traces (destructive; ask first).

## Run an eval (golden cases, real Bedrock; `EVAL_CONFIRM=1` on the paid steps)
`make eval-stack-up` → `make eval-start LABEL=<new-name>` → `make eval-health` → `make eval-smoke EVAL_CONFIRM=1` → `make eval-run EVAL_CONFIRM=1` → `make eval-freeze && make eval-grade` → `make eval-compare EVAL_BASELINE=metrics/evals/baselines/golden-2026-10-20.jsonl` → `make eval-cost`. Shortcut: `make eval-all LABEL=<name> EVAL_CONFIRM=1`. `EVAL_RUN=<run_id>` selects a run other than `metrics/evals/.current`. Runs live in `metrics/evals/<run_id>/` (gitignored). Details: `docs/evals.md`.

## Review a run (no Docker or Bedrock)
- `make eval-review-serve REVIEWER=<name> [EVAL_RUN=<run_id>] [EVAL_BASELINE=<results.jsonl>] [REVIEW_PORT=8765]` — local page; verdicts append to `<run>/reviews.jsonl`.
- `uv run --project tooling/evals evals run-review-export --root metrics/evals --run <run_id> --allow-incomplete` — resolve to `reviews.resolved.json` (coverage, disagreements).
- An older run with only `report/*.md` needs `evals run-grade` first (back up `report/`).
- `make eval-review` + `make eval-open` — static read-only report.

## Tests
`make evals-test` (offline eval tooling), `make test` (all suites), `make mcp-test`, `make web-test`, `make compose-smoke`, `make smoke`. Focused: `uv run --project services/app pytest services/app/tests/<file>`.

## Change workflow (see AGENTS.md)
- `git worktree add .worktrees/<issue>-<slug> -b feat/<issue>-<slug> main`; scratch state in `.vscode/myfiles/<issue>-<slug>/`.
- `gh pr create --draft`, `gh pr checks <n>`, `gh pr ready <n>`, then merge after review. Retitle/edit a PR with `gh api -X PATCH`, not `gh pr edit`.
- Hand-off: `make next-5-commands` reprints the try-it commands for the latest feature (see the `try-it-commands` skill).

Keep this file short: add a command only when it recurs; point to the Make target, not a copied recipe.
