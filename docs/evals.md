# Evaluation foundations

Scope: the offline `tooling/evals/` slice of #171, plus real `capture.v1` ingest and the session report (#176). The live direct-Bedrock vs LiteLLM comparison is still a follow-up.

**Status: the baseline/candidate/regressed fixtures (traces, run manifests, recorded judge scores) are synthetic. `fixtures/captured/` holds one recorded real-shaped session produced by the real loop with a scripted provider (NOT live Bedrock). All 37 golden cases are `pending_human_review`.** Nothing here is a live model observation or a human-approved reference yet.

**Experiment procedures:** [Live baselines, regressions, and model comparisons](evaluation/running-evaluations.md). The rest of this document covers evaluation architecture, golden cases, graders, capture/session reporting, and human review.

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

## Golden dataset: why these cases were chosen

The [versioned dataset](../tooling/evals/datasets/golden/v1/cases.jsonl) is an **agent-behavior benchmark for governed SQL analytics**, not a general-purpose document QA dataset. Each case records the user question (and follow-ups), expected behavior, allowed/expected tools and arguments, step limits, and—where relevant—reference SQL with expected facts. The reference SQL is hand-authored and the numerical facts are refreshed against pinned Parquet data; candidate model output is never automatically promoted to ground truth.

| Category | Cases | Actual example | Quality boundary being tested |
| --- | ---: | --- | --- |
| **Deterministic / positive** | 14 | “Which pickup zones have the most trips?” | Correct catalogue/tool selection, SQL-backed facts, bounded execution. |
| **ReAct / investigation** | 8 | “Do cash riders really tip less than card riders?” | Appropriate tools and caveats: cash tips are not recorded, so the answer must not treat missing tips as zero. |
| **Multi-turn** | 5 | “What are the busiest pickup hours?” → “Break that hour down by borough” | Resolve prior references and carry filters/analytical intent across turns; some cases deliberately expose unresolved basis differences. |
| **Unsupported / negative** | 6 | “Which driver earned the most money?”; “Delete all trips with zero distance from the dataset.” | Do not invent missing fields or unsupported causal/forecast claims; do not perform out-of-scope writes. |
| **Failure / recovery** | 4 | Tool validation error, timeout, empty result, provider error | Preserve failure semantics, disclose missing evidence, and avoid fabricated numbers on failure. |

These **positive, negative, multi-turn, and failure** cases test different failure modes. The file does **not currently define a dedicated typo/misspelling category or an adversarial prompt-injection suite**. Typos and paraphrases should be tested against tool-selection robustness, but we should add concrete cases before claiming that coverage.

### Rejection is not the same as prompt-injection protection

A request like “Delete all trips…” checks the application's **read-only tool boundary**. Questions about causes, future forecasts, outside periods, or nonexistent driver identifiers test **answerability and calibrated refusal**. Neither automatically constitutes a prompt-injection attempt.

Prompt injection requires an attacker-controlled instruction crossing a trust boundary—for this application, a realistic test would place instructions inside an **MCP tool result** and verify that the agent treats the result as data, never as a new system instruction. That needs separate adversarial cases and checks. Similarly, **internal instruction leakage** is a distinct output-quality/security failure and should be assessed explicitly rather than inferred from ordinary negative cases.

### Suggested dataset extensions (not implemented)

- **Typo/paraphrase robustness:** misspelled borough/metric names, shorthand, and reordered questions; measure valid tool choice and argument resolution.
- **Prompt-injection resistance:** malicious text in tool observations or other untrusted inputs; verify instruction hierarchy, tool authorization, and absence of leaked internal instructions.
- **Ambiguous follow-up resolution:** pronouns, comparisons, and inherited time/zone filters; measure whether the correct prior evidence is carried over.
- **Model/harness parity:** run the same approved cases on manual ReAct vs Deep Agents and large vs small planner models; compare tool accuracy, grounding, latency, and cost per correct answer.

**Review status:** All 37 cases are still `pending_human_review`. Baseline/candidate/regressed judge fixtures are synthetic, not proof of live model quality. Some references have different legacy/governed SQL filtering bases; adjudicate them before treating the dataset as authoritative for pass-rate comparisons.

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

## Grading outcomes and basis tags

**Tag vocabulary** (case-level `tags`, additive; golden values are never changed by tagging):

- `basis-dependent` plus exactly one of `basis:legacy_unfiltered` (legacy `query_taxi_data`, `average_trip_metrics`, `describe_taxi_dataset`: no valid-records filter), `basis:governed_valid_records` (`aggregate_taxi_data`, `compare_taxi_segments`, `list_taxi_dimension_values`) or `basis:mixed` (turns use tools from both bases). A basis-dependent reference can legitimately differ from another tool's answer to the same question, so reports separate cases by basis.
- `diagnostic`: graded and listed separately, excluded from aggregate rates and gates (reason in the case `notes`).
- `fault-injection`: the scenario needs an injected tool or provider failure and cannot be executed live; it is reported in its own section and excluded from live rates.

**Three separate outcomes** per turn: execution (did the workflow finish), tool (right tool and required arguments, or an accepted equivalent) and answer (facts, labels, completeness). Each is `correct`, `incorrect`, `needs_review` or `not_applicable`. `needs_review` is never counted as pass or fail; it has its own column and is resolved by a human.

**Tool output source**: `capture.v1` does not carry tool results, so checks that need them read an optional run-events directory (`context.reduced` events, joined by query id). Without it those checks report `needs_review` or `not_applicable`, never a silent pass.

**Grader v2 checks**:
- `required_args` / `forbidden_args`: verifies expected tool call arguments strictly beyond simple subset matching.
- `_truncated_incomplete`: detects truncated tool evidence (`truncated=True` or `row_count == limit`) on non-ranking queries and ensures the model does not present partial results as complete.
- `check_entity_value_association`: enforces clause/token window proximity between entity labels and numeric values.
- `check_comparative_claims`: verifies superlative/comparative claims (e.g. longest, shortest, highest) against actual row values.
- `ungrounded_numbers`: ensures numbers in answers originate from tool result rows rather than model hallucination.
- `tool_equivalence`: when a different tool provides verified reference values (e.g. `multi-004#0`), tool outcome is marked `needs_review` while answer is evaluated on its own.

**CLI usage**:
```bash
uv run --project tooling/evals evals session-report \
  --traces <traces.jsonl> \
  --matches <matches.json> \
  --events-dir <path_to_events> \
  --provisional
```

## Known gaps

- A first live provisional baseline exists only as local, uncommitted evidence; the committed session report is local-scripted. Its legacy grader score is not a trusted quality metric.
- `multi-001` carries the `diagnostic` tag: it is graded and listed in a separate "Diagnostic cases" section of delta reports, but excluded from the aggregate pass rate, tool-correctness mean, per-category rates and regression gates. It is retained for query-resolution (#110) diagnosis until the filter contract between `query_taxi_data` and the governed tools is reconciled. Its turn 2 asserts the entities (nested subset match on `baseline_filters`/`comparison_filters`: JFK Airport vs Midtown Center); subset matching recurses into nested objects, `exact` still requires identical keys.
- Golden cases are all pending human review, so strict mode grades nothing; reviewing `multi-001` is the next human step.
- Matching is manual by design; there is no automatic promotion of captured runs to golden cases.

Fixtures are synthetic, so no real model quality or cost decision is supported yet. The `app_runs` direct vs LiteLLM rerun, quality-qualified goodput joined with the benchmark SLO
goodput (here: pass rate only), failure clustering and judge calibration are follow-ups.

## Human review of a frozen run (#190)

Verdicts are stored beside the frozen evidence in `<run>/reviews.jsonl` (append-only, schema v1, bound to the run id and `captures.snapshot.sha256`); the evidence, golden cases and grader reports are never edited. A modified snapshot blocks reading and writing reviews.

1. `make eval-review-serve REVIEWER=<name> [EVAL_RUN=<run_id>] [EVAL_BASELINE=<results.jsonl>] [REVIEW_PORT=8765]` serves the review page on `127.0.0.1` only. Each turn has a tool / answer / resolution verdict (`correct`, `incorrect`, `needs_review`) plus notes; save writes to `reviews.jsonl`. The page filters by incomplete review (see below) and by grader/human disagreement; grader outcomes stay in their own column.
2. Re-reviewing a turn appends a new record; the latest `reviewed_at` wins and the earlier ones stay in the file as superseded. Duplicate ids, malformed lines and records bound to other evidence are reported, never applied.
A turn counts as fully reviewed when it has tool and answer verdicts, plus a resolution verdict on follow-up turns (`turn_index > 0`). That is a convention for the current baseline; per-case metadata in the golden cases would be the way to express more complex multi-turn expectations.
3. `evals run-review-export` writes `<run>/reviews.resolved.json` (verdicts, disagreements, coverage as untouched/partial/complete turns, malformed/stale/duplicate/orphaned records); `evals.reviews.load_human_verdicts` imports it. `evals run-review-add` records one verdict from the command line.

Both `make eval-review` and `make eval-review-serve` add a **run** column with each turn's run id and a **trace** link that opens Phoenix on that run's `ai.run` span (`PHOENIX_PORT` / `--phoenix-url`, `--phoenix-project-id` for a non-default project; default `Project:1`). The link pins Phoenix's time range to the run (the earliest and latest `run_started_at` in the frozen captures, plus 15 minutes either side; last 30 days if the run has none), so it also finds old runs, but Phoenix only has traces from when it was running: the 2026-10-20 baseline predates it, so its links open an empty list. The link is plain HTML, so a missing Phoenix or run id never blocks review. In Phoenix the same lookup by hand is the spans filter `attributes["ai"]["run_id"] == "run_..."` (attributes are nested, so `attributes["ai.run_id"]` matches nothing); a whole conversation is `attributes["session"]["id"] == "conv_..."`.

`make eval-review-report [EVAL_RUN=<run_id>]` (`evals run-review-report`) writes `<run>/review-report.md`: record counts (malformed, stale, duplicate, orphaned are listed, not applied), coverage (complete, partial, untouched), human verdict counts per dimension, grader-vs-human agreement with a table of disagreements (failure layer/category and notes when recorded), and a "still to review" queue ordered by how much it tells you: runs that did not complete, then turns the grader marked `needs_review`, then `incorrect`, then the rest as spot checks. The file does not refresh itself: its first line records when it was generated and the snapshot and reviews digests, so re-run the target after saving verdicts. Verdicts on a dimension that is not required for a turn (for example `resolution` on turn 0) are shown as optional and do not count toward coverage. `make eval-compare` adds the same human section under the grader tables once `reviews.jsonl` exists; grader and human numbers are never merged.

### Adjudicating a frozen baseline

1. Make sure the run has graded JSON reports (`<run>/report/*.json`). Older runs have only `.md`: back up `report/` and run `evals run-grade` (the Markdown is unchanged).
2. `make eval-review-serve REVIEWER=<name> EVAL_RUN=<run_id> EVAL_BASELINE=<results.jsonl>` and work through the "incomplete review" filter; use the **trace** link where Phoenix has the run (traces exist only from when Phoenix was running).
3. `make eval-review-report EVAL_RUN=<run_id>` to see what is left and where you disagree with the grader. The baseline is adjudicated when "Still to review" and "Unresolved human needs_review" both say none (a `needs_review` verdict counts as annotated but not as adjudicated; record a later verdict to resolve it); then `evals run-review-export` writes `reviews.resolved.json` for downstream use.
4. Golden cases and expected values are **not** edited here: a human verdict that the reference or grader is wrong (for example `det-009`, `multi-004`) goes into the notes and a PR that changes the golden case or grader.
