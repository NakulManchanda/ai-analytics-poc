# Work 0139 — Disjoint splits, offline CI gate, and development prompt A/B (partial #179, Epic #167)

## Goal

Advance Issue #179 (Epic #167) by adding a versioned, disjoint `dev`/`holdout` split sidecar with authoritative runner enforcement, adding a zero-cost offline recorded-fixture CI regression gate + programmatic holdout-leakage check, and running one matched live ReAct development A/B (`planner-single-turn@1.2.0` vs candidate `1.3.0`) on pinned model/dataset/tool schemas with reconciled LiteLLM spend and Phoenix trace continuity. The sealed-holdout promotion criterion in #179 stays open until a candidate passes development evaluation.

## Starting point

`main` at `e25d95e`. Epic #167 children `#168`, `#169` (deployed ECS `s3_v2` capture closed as not planned in `#256`), `#170`, `#176`, `#178`, and `#180` were shipped and closed. `cases.jsonl` had 37 golden cases with no split assignment. Frozen Nova Pro ReAct runs (`oct30-nova-pro`, `oct31-nova-pro`) showed unsupported causal/forecast/missing-field turns (`uns-001`, `uns-002`, `uns-003`, `multi-005#1`) invoking unneeded analytics tools.

## Decisions

- **Versioned sidecar `splits.json` (`tooling/evals/datasets/golden/v1/splits.json`):** Kept `cases.jsonl` byte-identical to `main` so `dataset_sha256` does not churn and frozen baselines (`2026-10-20`, `oct30-nova-pro`, `oct31-nova-pro`) remain directly comparable. `splits.json` partitions all 37 cases into disjoint `dev` (20) and `holdout` (17) sets with explicit rationale: the motivating unsupported/causal failure-cluster cases (`uns-001`, `uns-002`, `uns-003`, `multi-005`) sit in `dev`, while `uns-004`, `uns-005`, `uns-006`, `multi-003`, `det-008..014`, `react-005..008`, `fail-002`, and `fail-004` form the sealed `holdout` split. `validate_splits()` enforces exact partition coverage (no silent default), zero overlap, and all 5 categories in both splits. `evals run-start --split` uses `DEFAULT_DATASET` / `DEFAULT_SPLITS` and records `splits_sha256` in `manifest.json`.
- **Zero-cost offline CI gates (pytest, no Promptfoo dependency):**
  - Behavioral recorded-fixture gate (`tooling/evals/tests/test_prompt_regression_gate.py`): runs `evals.report.compare()` over `fixtures/baseline` vs `fixtures/candidate` (pass) and `fixtures/regressed` (fail) with zero paid model calls, plus a programmatic disjointness check that loads every `holdout` turn question from `load_cases()` + `load_splits()` and verifies none appear in `services/app/app/prompts/bundles.py`.
  - Fast prompt-contract lint (`services/app/tests/test_prompt_regression_gate.py`): checks the real `taxi_proposal_prompt` renderer for required clauses and proves mutations fail fast.
- **Matched live ReAct development A/B (`planner-single-turn@1.2.0` vs candidate `1.3.0`):**
  - Runtime: bespoke ReAct (`AGENT_STRATEGY=manual`), isolated Compose project `eval_179` (`WEB_PORT=13000`, `LITELLM_PORT=14001`, `PHOENIX_PORT=16007`; existing `ai_app_poc` stack untouched).
  - Pinned inputs: `taxi-planner` = Bedrock Nova Pro, `taxi-answer` = Bedrock Nova Micro, Jan-2024 parquet, same 6 governed MCP tools, same 18-case / 21-turn development slice (`det-008..014`, `react-005..008`, `multi-003`, `multi-005`, `uns-002`, `uns-003`, `uns-005`, `fail-002`, `fail-004`).
  - Method note: this development A/B slice included three of the motivating cases (`uns-002`, `uns-003`, `multi-005`) that now live in `dev` in `splits.json` (`20 dev / 17 holdout`). Because `1.3.0` failed to improve even on those motivating cases and introduced a regression, it was rejected early without running on the sealed `holdout` split. The sealed-holdout acceptance criterion in #179 remains open for a future candidate.

### Archived rejected candidate text (`planner-single-turn@1.3.0`)

Single variable changed vs `1.2.0` (only the unsupported-scope decline sentence):

- **Baseline `1.2.0`:**
  > `For a question about causes that depend on data outside the dataset (such as weather, events, traffic or news), answer in one plain sentence that the dataset does not contain that information, and do not use a tool.`
- **Candidate `1.3.0` (rejected):**
  > `For a question asking why something happened, asking for a future forecast or prediction, or asking about fields or time periods not present in the dataset schema (such as weather, events, traffic, news, or individual people), answer in one plain sentence that the dataset does not contain that information, and do not use a tool.`

## Measured live A/B results (18 cases / 21 turns, single trial per arm)

| Metric | Baseline `1.2.0` (`179-baseline-v120`) | Candidate `1.3.0` (`179-candidate-v130`) | Delta |
|---|---|---|---|
| Execution (`correct / incorrect / n/a`) | `19 / 0 / 2` | `18 / 1 / 2` | `-1` (`multi-005#1` failed with `multiple_tool_calls`) |
| Tool (`correct / incorrect / n/a`) | `12 / 7 / 2` | `13 / 6 / 2` | `+1` (artifact of `multi-005#1` failing pre-tool, not a clean decline) |
| Answer (`correct / needs_review / incorrect / n/a`) | `6 / 5 / 8 / 2` | `6 / 5 / 8 / 2` | `0` net (`react-008#0` improved `+1`, `multi-003#0` regressed `-1` on truncation) |
| Model calls / tokens (`in / out`) | `33` calls (`62,202 / 5,121`) | `32` calls (`62,129 / 5,144`) | `-1` call, `-73` in, `+23` out |
| Turn wall time (`median / p95 / max`) | `6.47s / 10.59s / 12.57s` | `6.43s / 9.57s / 11.35s` | `-0.04s` median, `-1.02s` p95 |
| Streaming TTFT (`median / max`) | `321ms / 1327ms` (`n=19`) | `308ms / 1213ms` (`n=18`) | `-13ms` median |
| Reconciled LiteLLM spend | `$0.051022` (`ok=True`, 33 rows, 0 orphans) | `$0.051294` (`ok=True`, 32 rows, 0 orphans) | `+$0.000272` (`+0.5%`) |
| Phoenix trace continuity | `21/21` runs, `0` problems | `21/21` runs, `0` problems | `0` |

### Failure-cluster breakdown on target cases

- `uns-002` (forecast) and `uns-005` (Jan 2023): already declined cleanly (`unsupported`) under both `1.2.0` and `1.3.0`.
- `uns-003` (missing field: individual driver): both arms called `aggregate_taxi_data(dimensions=["vendor"])` instead of declining.
- `multi-005#1` (causal follow-up "Why is it so much bigger than the others?"): `1.2.0` called `average_trip_metrics({})`; `1.3.0` regressed to `run.failed` (`multiple_tool_calls`).

## Recommendation: REJECT `1.3.0`, keep `1.2.0`

`1.3.0` delivered zero genuine decline improvements on the motivating cluster and introduced one `multiple_tool_calls` execution failure at virtually identical spend. `planner-single-turn@1.2.0` remains production.

## Known limitations

- Single live trial per arm (`n=1`); small stochastic swings (such as the two offsetting answer deltas on `react-008#0` and `multi-003#0`) are not statistically significant on 18 cases.
- Because `1.3.0` was rejected on the motivating cluster, no sealed-holdout promotion run was needed.

## Verification

- `uv run --project services/app pytest services/app/tests/test_prompts.py services/app/tests/test_prompt_attribution.py services/app/tests/test_prompt_regression_gate.py -q`
- `make evals-test` (including `test_dataset.py`, `test_run_folder.py`, and `test_prompt_regression_gate.py`)
- `ruff check` and `black --check` across modified `services/app` and `tooling/evals` files
- Live A/B (`179-baseline-v120` vs `179-candidate-v130`) with `eval-reconcile` (`ok=True`, `0` orphans) and `eval-trace-check` (`21/21`, `0` problems); isolated `eval_179` stack torn down afterward.
