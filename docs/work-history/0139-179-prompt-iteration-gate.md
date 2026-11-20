# Work 0139 — Disjoint splits, fresh unseen holdout, offline CI gates, and live ReAct prompt A/Bs (#179, Epic #167)

## Goal

Complete Issue #179 (Epic #167) by:
1. Partitioning `golden/v1` into `dev` (20) and `regression` (17) while freezing a fresh, unseen 10-case / 11-turn `holdout` dataset (`tooling/evals/datasets/holdout/v1/cases.jsonl`) with independent DuckDB SQL reference facts and zero overlap with `golden/v1` or prompt bundles.
2. Enforcing manifest splits authoritatively in both live and Temporal runners and making `make eval-run` / `make eval-temporal-run` use the saved manifest split automatically without repeating `EVAL_SPLIT`.
3. Adding zero-cost offline CI regression gates (recorded-fixture delta gate + programmatic holdout-leakage check tracked by Nx `evals:test`, plus fast prompt-contract lint).
4. Running matched live Bedrock ReAct A/B evaluations with reconciled LiteLLM spend and Phoenix trace continuity: rejecting `planner-single-turn@1.3.0` early on the development slice, and evaluating a new single-variable candidate (`answer@1.1.0` / `answer-with-schema@1.1.0`) against `answer@1.0.0` on the fresh unseen holdout.

## Decisions

- **3-way disjoint splits (`tooling/evals/datasets/golden/v1/splits.json`):** `golden/v1/cases.jsonl` stays byte-identical to `main` (preserving `dataset_sha256` for frozen baselines). `splits.json` (`v2`) partitions `golden/v1` into `dev` (20, including motivating unsupported/causal cases `uns-001..003` and `multi-005`) and `regression` (17 previously exercised v1 cases), and points `holdout` to 10 fresh unseen cases (`hold-001`..`hold-010`, 11 turns, 20 DuckDB-verified reference facts) in `tooling/evals/datasets/holdout/v1/cases.jsonl`. `validate_splits()` enforces exact partition coverage, zero ID overlap, and all 5 categories in every split.
- **Authoritative split enforcement + Make defaults:** `live_run.py` and `temporal_store.py` reject `--all` and out-of-split `case_ids` on split-pinned manifests before issuing any paid request. `Makefile`'s `EVAL_DEFAULT_ALL` reads the active run's `manifest.json` so `make eval-run` and `make eval-temporal-run` omit `--all` automatically when `eval-start` saved a split/case list (`test_make_eval_targets.py`).
- **Offline CI gates + Nx input tracking:** `tooling/evals/tests/test_prompt_regression_gate.py` runs `evals.report.compare()` over `fixtures/baseline` vs `fixtures/candidate` (pass) and `fixtures/regressed` (fail) with zero paid calls, plus a programmatic check that zero `holdout` turn questions appear in `services/app/app/prompts/bundles.py` or `golden/v1/cases.jsonl`. `nx.json` adds `promptBundles` (`{workspaceRoot}/services/app/app/prompts/bundles.py`) to `evals:test` inputs so prompt-only edits select `app` and `evals`.

---

## Experiment 1 (Development A/B — REJECTED): `planner-single-turn@1.2.0` vs `1.3.0`

- **Slice:** 18 cases / 21 turns (`det-008..014`, `react-005..008`, `multi-003`, `multi-005`, `uns-002`, `uns-003`, `uns-005`, `fail-002`, `fail-004`; included three motivating dev cases). Runtime: `AGENT_STRATEGY=manual`, Nova Pro planner + Nova Micro answer, isolated `eval_179` stack.
- **Single variable tested (archived verbatim, reverted):**
  - `1.2.0`: `For a question about causes that depend on data outside the dataset (such as weather, events, traffic or news), answer in one plain sentence that the dataset does not contain that information, and do not use a tool.`
  - `1.3.0` (rejected): `For a question asking why something happened, asking for a future forecast or prediction, or asking about fields or time periods not present in the dataset schema (such as weather, events, traffic, news, or individual people), answer in one plain sentence that the dataset does not contain that information, and do not use a tool.`

| Metric (18 dev/reg cases / 21 turns, n=1) | Baseline `1.2.0` (`179-baseline-v120`) | Candidate `1.3.0` (`179-candidate-v130`) | Delta |
|---|---|---|---|
| Execution (`correct / incorrect / n/a`) | `19 / 0 / 2` | `18 / 1 / 2` | `-1` (`multi-005#1` `multiple_tool_calls`) |
| Tool (`correct / incorrect / n/a`) | `12 / 7 / 2` | `13 / 6 / 2` | `+1` (pre-tool failure artifact, not decline) |
| Answer (`correct / needs_review / incorrect / n/a`) | `6 / 5 / 8 / 2` | `6 / 5 / 8 / 2` | `0` net (`react-008#0` `+1`, `multi-003#0` `-1`) |
| Model calls / tokens (`in / out`) | `33` (`62,202 / 5,121`) | `32` (`62,129 / 5,144`) | `-1` call |
| Wall time (`median / p95`) / TTFT (`median`) | `6.47s / 10.59s` / `321ms` | `6.43s / 9.57s` / `308ms` | `-0.04s` / `-13ms` |
| Reconciled LiteLLM spend | `$0.051022` (`ok=True`, 0 orphans) | `$0.051294` (`ok=True`, 0 orphans) | `+$0.000272` |
| Phoenix trace continuity | `21/21` runs, `0` problems | `21/21` runs, `0` problems | `0` |

**Decision:** **REJECT `planner-single-turn@1.3.0`** early (`uns-003` still queried `vendor`; `multi-005#1` regressed to `multiple_tool_calls`). Kept `planner-single-turn@1.2.0`.

---

## Experiment 2 (Fresh Unseen Holdout A/B — SHIPPED): `answer@1.0.0` vs `answer@1.1.0`

- **Motivating failure cluster (from Experiment 1 baseline):** `det-009#0`, `react-008#0`, `det-010#0`, and `det-014#0` all had `tool=correct` but `answer=incorrect` due to comparative superlative inversion, row/metric label-pairing mismatches, or ungrounded derived figures on the Nova Micro answer call (`answer@1.0.0`).
- **Single variable changed (`answer` & `answer-with-schema` `1.0.0 → 1.1.0`, `planner-single-turn@1.2.0` unchanged):** appended `_ANSWER_GROUNDING_RULE` to the global instruction region:
  > `When comparing groups, verify which row has the highest or lowest value directly from the query result before naming it, pair every number with its exact row and metric label, and cite only numbers present in the query result.`
- **Dataset:** Fresh, unseen 10-case / 11-turn `holdout` split (`tooling/evals/datasets/holdout/v1/cases.jsonl`, `hold-001`..`hold-010`), executed via `make eval-start EVAL_SPLIT=holdout` + `make eval-run EVAL_CONFIRM=1` (no `EVAL_SPLIT` repeated) on isolated `eval_179_ho` stack (`AGENT_STRATEGY=manual`, Nova Pro planner + Nova Micro answer).

| Metric (10 fresh holdout cases / 11 turns, n=1) | Baseline `answer@1.0.0` (`179-holdout-base-a100`) | Candidate `answer@1.1.0` (`179-holdout-cand-a110`) | Delta |
|---|---|---|---|
| Prompts observed in capture | `planner-single-turn@1.2.0` (11), `answer@1.0.0` (8) | `planner-single-turn@1.2.0` (11), `answer@1.1.0` (8) | Single variable |
| Execution (`correct / n/a`) | `10 / 1` (0 failures) | `10 / 1` (0 failures) | `0` |
| Tool (`correct / incorrect / n/a`) | `8 / 2 / 1` | `8 / 2 / 1` | `0` |
| Answer (`correct / needs_review / incorrect / n/a`) | `2 / 2 / 6 / 1` | `3 / 2 / 5 / 1` | `+1` (`hold-006#0` fixed, `0` regressions) |
| Model calls / tokens (`in / out`) | `19` (`46,077 / 2,001`) | `19` (`46,416 / 2,035`) | `+339` in (`+0.7%`), `+34` out |
| Wall time (`median / p95`) | `5.38s / 8.74s` | `5.77s / 7.29s` | `+0.39s` median, `-1.45s` p95 |
| Streaming TTFT (`median / max`, `n=8`) | `295ms / 434ms` | `322ms / 387ms` | `+27ms` median, `-47ms` max |
| Reconciled LiteLLM spend | `$0.039245` (`ok=True`, 19 rows, 0 orphans) | `$0.039232` (`ok=True`, 19 rows, 0 orphans) | `-$0.000013` (`-0.03%`) |
| Phoenix trace continuity | `11/11` runs, `0` problems | `11/11` runs, `0` problems | `0` |

**Decision:** **SHIP `answer@1.1.0` / `answer-with-schema@1.1.0`**. Fixed the exact label-to-number association failure on `hold-006#0` with zero regressions and virtually identical cost (`-$0.000013`).

## Known limitations

- Single live trial per arm (`n=1`) on 10 fresh holdout cases; directional win with zero regressions, not a statistically powered sample.
- Fresh holdout cases (`hold-001`..`hold-010`) have DuckDB-verified reference SQL and are queued for final human review sign-off (`pending_human_review`).

## Verification

- `uv run --project services/app pytest services/app/tests/test_prompts.py services/app/tests/test_prompt_attribution.py services/app/tests/test_prompt_regression_gate.py -q`
- `make evals-test` (including `test_make_eval_targets.py`, `test_dataset.py`, `test_run_folder.py`, `test_run_live.py`, `test_prompt_regression_gate.py`)
- `uv run --project tooling/evals --extra temporal pytest tooling/evals/tests/test_temporal_pilot.py -k test_temporal_resolve_batch_plan_enforces_manifest_split -q`
- `npx nx show projects --affected --files=services/app/app/prompts/bundles.py` -> `app`, `evals`
- `ruff check` and `black --check` clean across modified `services/app` and `tooling/evals` files
- Isolated `eval_179` and `eval_179_ho` Compose projects torn down cleanly; `ai_app_poc` untouched.
