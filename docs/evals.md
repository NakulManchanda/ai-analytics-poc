# Evaluation foundations

Scope: the offline `tooling/evals/` slice of #171, plus real `capture.v1` ingest and the session report (#176). The live direct-Bedrock vs LiteLLM comparison is still a follow-up.

**Status: the baseline/candidate/regressed fixtures (traces, run manifests, recorded judge scores) are synthetic. `fixtures/captured/` holds one recorded real-shaped session produced by the real loop with a scripted provider (NOT live Bedrock). All 37 golden cases are `pending_human_review`.** Nothing here is a live model observation or a human-approved reference yet.

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

## Captured traffic: candidate -> reviewed golden -> comparison (#176)

Real `capture.v1` JSONL has `run_id`/`turn_id`/`call_id`/`conversation_id` but no `case_id`/`turn_index`.

1. `evals candidates --traces captured.jsonl` (needs question text, i.e. a capture made with `CAPTURE_CONTENT=1`; metadata-only captures have no text to match and must be linked by a human-written matches file) lists each run with *suggested* golden turns (exact question match; may be several). A suggestion is not a label.
2. A human writes a matches file (`evals.matches.v1`: `run_id`, `case_id`, `turn_index`, `confirmed_by`). Validation rejects unknown cases, bad turn indexes, a missing `confirmed_by`, and duplicate runs or targets.
3. `resolve()` grades a matched run only if the golden case is `review.status == "reviewed"`. Runs that are unmatched, or matched to a still-pending case, stay *candidate only* and are not graded. `--provisional` grades against pending references and the report is then marked PROVISIONAL.
4. `evals session-report --traces ... --matches ... [--conversation-id ID] [--provisional]` writes the per-session report: answer correctness, tool behavior (actual vs expected), tokens, cost, latency. LIVE is derived only from the `execution_mode` recorded in the capture and requires every record to say `live`; anything else (scripted, unverified, missing) prints NOT LIVE. `--origin live` is only a cross-check and is refused when the capture is not recorded as live. The file must hold exactly one conversation, or select one with `--conversation-id`; otherwise the command errors. Unknown tokens/cost render `n/a` and totals are marked as lower bounds.

`build_turns()` no longer raises on unlinked records; it skips them (use `build_run_views()` for raw capture). Tool status `completed` (the app) and `ok` (synthetic) both count as success.

`fixtures/captured/taxi-session.jsonl` is a two-turn session (`multi-001`) recorded by `services/app/tests/capture_session_fixture.py`: real `OrchestrationLoop`, `LiteLLMClient`, model policy/pricing and capture recorder; scripted provider (httpx mock) and scripted MCP. Answers, token counts and latency are scripted, so correctness passing is by construction; it proves the pipeline, not model quality. `matches.json` there is fixture wiring by the agent, not a golden review. `report.md` is the PROVISIONAL report for it. Replace with a live Bedrock capture (same commands; it reports LIVE only if the capture was recorded with `CAPTURE_EXECUTION_MODE=live`, which is an operator assertion made at capture time) once a run is available.

## Known gaps

- No live Bedrock session has been captured or reported yet; the committed session report is local-scripted.
- Golden cases are all pending human review, so strict mode grades nothing; reviewing `multi-001` is the next human step.
- Matching is manual by design; there is no automatic promotion of captured runs to golden cases.

Fixtures are synthetic, so no real model quality or cost decision is supported yet. The `app_runs` direct vs LiteLLM rerun, quality-qualified goodput joined with the benchmark SLO
goodput (here: pass rate only), failure clustering and judge calibration are follow-ups.
