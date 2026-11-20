# Work 0139 — Eval-driven prompt iteration, disjoint holdout split, and offline CI gate (#179, Epic #167)

## Goal

Complete Issue #179 and close Epic #167 by adding disjoint `dev`/`holdout` splits to the golden dataset, adding a zero-cost offline deterministic CI prompt-regression gate, and running one matched live ReAct prompt A/B on pinned model/dataset/tool schemas with reconciled LiteLLM spend and Phoenix trace continuity.

## Starting point

`main` at `e25d95e`. Epic #167 children `#168`, `#169` (ECS `s3_v2` capture deferred to `#256`), `#170`, `#176`, `#178`, and `#180` were shipped and closed. `cases.jsonl` had 37 golden cases with no `split` field. Frozen Nova Pro ReAct runs (`oct30-nova-pro`, `oct31-nova-pro`) showed unsupported causal/forecast/missing-field turns (`uns-001`, `uns-002`, `uns-003`, `multi-005#1`) invoking unneeded analytics tools.

## Decisions

- **Disjoint dev/holdout split (`tooling/evals/`):** Added `split: Literal["dev", "holdout"]` to `GoldenCase` (`schema.py`), validated that both splits are non-empty and cover all 5 categories (`dataset.py`), populated `cases.jsonl` with 19 `dev` and 18 `holdout` cases (keeping all multi-turn scenarios grouped whole), and added `--split` to `evals run-start` + `EVAL_SPLIT` to `Makefile`. Regenerated synthetic fixture manifests for the updated dataset SHA-256.
- **Offline CI prompt-regression gate (pytest, no Promptfoo dependency):** Added `services/app/tests/test_prompt_regression_gate.py`, which validates the real `taxi_proposal_prompt` renderer against required behavioral/safety invariants (single-tool constraint, multi-tool prohibition, read-only decline, unsupported-scope decline, positive tool anchor, no leaked `"no tool"` phrase, zero holdout leakage) and proves via 5 parametrized mutations that intentional regressions fail CI with zero paid model calls.
- **Matched live ReAct holdout A/B (`planner-single-turn@1.2.0` vs candidate `1.3.0`):**
  - Runtime: bespoke ReAct (`AGENT_STRATEGY=manual`), isolated Compose project `eval_179` (`WEB_PORT=13000`, `LITELLM_PORT=14001`, `PHOENIX_PORT=16007`; existing `ai_app_poc` stack untouched).
  - Pinned inputs: `taxi-planner` = Bedrock Nova Pro, `taxi-answer` = Bedrock Nova Micro, Jan-2024 parquet, same 6 governed MCP tools, same 18 holdout cases (21 turns).
  - Single variable tested in `1.3.0`: expanded the unsupported-scope decline clause to mention causal "why" questions, future forecasts/predictions, and fields/time periods not in the dataset schema (zero holdout leakage).

## Measured holdout results

| Metric (18 holdout cases / 21 turns) | Baseline `1.2.0` (`179-baseline-v120`) | Candidate `1.3.0` (`179-candidate-v130`) | Delta |
|---|---|---|---|
| Execution (`correct` / `incorrect` / `n/a`) | `19 / 0 / 2` | `18 / 1 / 2` | `-1` (`multi-005#1` failed with `multiple_tool_calls`) |
| Tool (`correct` / `incorrect` / `n/a`) | `12 / 7 / 2` | `13 / 6 / 2` | `+1` (artifact of `multi-005#1` failing pre-tool, not a clean decline) |
| Answer (`correct` / `needs_review` / `incorrect` / `n/a`) | `6 / 5 / 8 / 2` | `6 / 5 / 8 / 2` | `0` (`react-008#0` improved, `multi-003#0` regressed on truncation) |
| Reconciled LiteLLM spend | `$0.051022` (`ok=True`, 33 rows, 0 orphans) | `$0.051294` (`ok=True`, 32 rows, 0 orphans) | `+$0.000272` (`+0.5%`) |
| Phoenix trace continuity | `21/21` runs, `0` problems | `21/21` runs, `0` problems | `0` |

## Recommendation: REJECT `1.3.0`, keep `1.2.0`

- `uns-002` (forecast) and `uns-005` (Jan 2023) already declined cleanly under `1.2.0`.
- `uns-003` ("Which driver earned the most money?") still mapped to `aggregate_taxi_data(dimensions=["vendor"])` under `1.3.0`.
- `multi-005#1` ("Why is it so much bigger than the others?") regressed from a completed run to `run.failed` (`multiple_tool_calls`) rather than declining cleanly.
- Therefore, `1.3.0` is **rejected** and reverted; `planner-single-turn@1.2.0` remains production, and this PR ships the disjoint splits, `--split` runner support, offline regression gate, and documented experiment record.

## Verification

- `uv run --project services/app pytest services/app/tests -q`
- `make evals-test`
- `ruff check` and `black --check` across modified `services/app` and `tooling/evals` files
- Live holdout A/B (`179-baseline-v120` vs `179-candidate-v130`) with `eval-reconcile` (`ok=True`, `0` orphans) and `eval-trace-check` (`21/21`, `0` problems); isolated `eval_179` stack torn down afterward.

## Lessons

- Holdout A/B testing prevented shipping a plausible-looking prompt edit that produced zero real decline wins and introduced a `multiple_tool_calls` execution failure.
- LiteLLM `/spend/logs` flushes asynchronously; waiting a few seconds after `eval-run` before `eval-reconcile` avoids transient missing-row violations.
