# Evaluation foundations

Scope: the offline `tooling/evals/` slice of #171. Live capture ingest (#169) and the live
direct-Bedrock vs LiteLLM comparison are follow-ups; this slice runs on committed golden cases and
synthetic `capture.v1` fixtures.

**Status: all fixtures (traces, run manifests, recorded judge scores) are synthetic, and all 37 golden cases are `pending_human_review`.** Nothing here is a real model observation or a human-approved reference yet.

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

## Dataset v1 (37 cases)

14 deterministic catalogue questions, 8 ReAct investigations, 5 multi-turn conversations, 6
unsupported/causal requests, 4 failure scenarios (tool validation error, timeout, empty result,
provider error). Every `review.status` is `pending_human_review` until the PR reviewer promotes it.
Facts use tolerances (counts 0.1%, averages 1%) and accept `23.8%` for `0.2376`.

## Judge metrics and their limits

Metrics: answer quality (GEval), tool correctness (DeepEval, deterministic), faithfulness, task
completion, step efficiency. Normal CI never calls a model: `RecordedJudge` replays stored scores.
Live scoring needs `uv sync --extra judge`, `EVALS_LIVE_JUDGE=1` and a judge model; it costs money and
is unverified against a real judge in this slice.

- Judges are probabilistic, drift across judge-model versions, and favour fluent or long answers.
  Treat scores as a screening signal, not ground truth; deterministic graders gate numeric claims.
- Compare runs only under the same judge model and version (stored in the manifest).
- Faithfulness here uses the reviewed reference facts as evidence because `capture.v1` stores row
  counts and query ids, not raw tool results. It does not detect a correct number that the run never
  retrieved; the `evidence_present` check covers that.
- Judge-vs-human calibration needs a hand-labeled set; not done yet. Recorded judge scores in
  `fixtures/` are synthetic.
- Small dataset: a one-case swing moves pass rate ~2.7 points. Thresholds are initial guesses.

## Known gaps

Fixtures are synthetic, so no real model quality or cost decision is supported yet. Ingest of real
JSONL, the `app_runs` direct vs LiteLLM rerun, quality-qualified goodput joined with the benchmark SLO
goodput (here: pass rate only), failure clustering and judge calibration are follow-ups.
