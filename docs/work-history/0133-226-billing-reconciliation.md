# 0133 — Eval billing reconciliation against LiteLLM spend (#226, parent #224)

## Goal
Make eval cost trustworthy for #228: reconcile a run's frozen capture with the LiteLLM `/spend/logs` rows so billed spend the run record never saw (for example a request still in flight when its run was cancelled) is visible and counted.

## Starting point
`billing_complete` only covers calls the run recorded; the join invariants in `docs/evals.md` (capture `call_id` to spend rows, `(call_id, attempt)` unique) were documented but not checked by any tool.

## Decisions
- Offline-testable pure function `evals.spend.reconcile` over capture `model_call` records and spend rows; only rows tagged `eval_run_id:<id>` are used, so untagged traffic is never counted.
- Spend is summed per `call_id` (policy fallbacks and retries give several rows per call).
- Violations (exit 4): capture call with no spend row, repeated `(call_id, attempt)`, provider-billed capture cost differing from spend, or no tagged rows at all (an empty pull must not pass). Orphan rows are reported, not failed: they are billed spend, added to `reconciled_cost_usd`, which is the cost #228 should use.
- Calls whose capture cost is not provider-billed are counted in `estimated_calls` and not cost-compared.
- `evals run-reconcile` reads `<run>/captures.snapshot.jsonl` and `<run>/spend_logs.json`, writes `report/spend_reconciliation.json`; `make eval-reconcile` pulls `/spend/logs` first.

## Verification
- `uv run --project tooling/evals pytest tooling/evals/tests`: 180 passed, 2 skipped; black and ruff clean; `make -n eval-reconcile` expands as intended.
- New tests: clean run, fallback 1:N, cancel orphan, row without call metadata, missing spend row, duplicate attempt, billed mismatch vs estimated, untagged rows ignored, CLI exit codes.
- Not verified: a live run against a real proxy. Open question for that run: whether LiteLLM writes and tags a spend row for a cancelled in-flight request (the tool handles both outcomes).

## Known limits
`/spend/logs` returns recent rows only, so run `eval-reconcile` right after `eval-freeze` with the stack still up. The Phoenix trace-continuity check is the separate next slice.

## PR / merge state
Branch `feat/226-billing-reconciliation`; PR references #226.

## Lessons
A cost number that only counts recorded calls under-reports exactly when runs are cancelled; reconcile against the provider-side ledger, not the app's own record.
