# Evaluation architecture

[Evaluation documentation index](../evals.md)

## Feedback loop

1. **Observe**: runs emit `capture.v1` JSONL (one record per model or tool call, joined by `run_id`).
2. **Triage**: failures and expensive or slow runs are clustered; interesting ones become *candidate*
   cases. Production/demo observations are never golden by themselves.
3. **Review**: a human writes the reference SQL, `evals refresh-reference` computes the expected facts
   on the pinned parquet, and the case lands in `datasets/golden/<version>/` through a PR. A model
   answer or model SQL is never ground truth.
4. **Grade**: deterministic graders (numeric facts, tool arguments, step budget, evidence present,
   limitation stated, no fabricated numbers) run on every PR over recorded fixtures. DeepEval judge
   metrics are replayed from recorded verdicts.
5. **Compare**: `evals report` diffs a candidate run against a baseline using the thresholds pinned in
   the run manifest (dataset version and hash, model alias, prompt version, judge model, thresholds).
   Regressions exit non-zero. Cost, tokens, latency and failure rate are reported next to quality.
6. **Decide and grow**: ship or reject the model/prompt change, then add reviewed cases for new
   failure clusters and bump the dataset version (never edit a released version silently).


## Known gaps

- A first live provisional baseline exists only as local, uncommitted evidence; the committed session report is local-scripted. Its legacy grader score is not a trusted quality metric.
- `multi-001` carries the `diagnostic` tag: it is graded and listed in a separate "Diagnostic cases" section of delta reports, but excluded from the aggregate pass rate, tool-correctness mean, per-category rates and regression gates. It is retained for query-resolution (#110) diagnosis until the filter contract between `query_taxi_data` and the governed tools is reconciled. Its turn 2 asserts the entities (nested subset match on `baseline_filters`/`comparison_filters`: JFK Airport vs Midtown Center); subset matching recurses into nested objects, `exact` still requires identical keys.
- Golden cases are all pending human review, so strict mode grades nothing; reviewing `multi-001` is the next human step.
- Matching is manual by design; there is no automatic promotion of captured runs to golden cases.

Fixtures are synthetic, so no real model quality or cost decision is supported yet. The `app_runs` direct vs LiteLLM rerun, quality-qualified goodput joined with the benchmark SLO
goodput (here: pass rate only), failure clustering and judge calibration are follow-ups.

