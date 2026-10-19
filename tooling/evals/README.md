# tooling/evals

Offline evaluation tooling for the AI Analytics POC (issue #171, foundation slice). See
[docs/evals.md](../../docs/evals.md) for the feedback loop and judge-metric limitations.

```
make evals-test                           # all tests, recorded fixtures, no paid model
make evals-report                         # baseline vs candidate delta (exit 0)
make evals-report CANDIDATE=regressed     # same, trips thresholds (exit 2)
uv run --project tooling/evals evals validate
# recompute reference facts from the hand-authored SQL (needs duckdb + the pinned parquet):
uv run --project tooling/evals --extra reference evals refresh-reference --data-dir data/nyc-yellow-taxi-2024-01
EVALS_DATA_DIR=<dir> uv run --project tooling/evals pytest   # also runs the reference-drift test
```

Captured traffic (#176; see docs/evals.md): `evals candidates --traces F`, then
`evals session-report --traces F --matches M [--conversation-id ID] [--provisional]` (LIVE comes only from the capture's recorded `execution_mode`; one conversation per report). Candidate suggestion by question text needs `CAPTURE_CONTENT=1`; metadata-only captures need a human-written `evals.matches.v1` file.
`fixtures/captured/` is a recorded NOT LIVE session (scripted provider) with its matches and report.

Layout: `datasets/golden/v1/cases.jsonl` (37 reviewed-by-PR golden cases), `src/evals/` (schema,
dataset, traces, graders, judge, manifest, report, synth, cli), `fixtures/{baseline,candidate,regressed}/`
(synthetic `capture.v1` traces + recorded judge scores + run manifest; regenerate with `evals synth`,
a test fails if they drift from the generator).

Golden cases are never model-generated: reference SQL is hand-authored, fact values come from running
it on the pinned DuckDB dataset, and new cases enter through PR review (`review.status`).
