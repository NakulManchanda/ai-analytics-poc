# Evaluation System

The NYC Taxi analytics evaluation system tests **answer quality, tool correctness, multi-turn behavior, scope enforcement, and failure handling**. It combines versioned golden cases, captured run evidence, automated graders, and a human review workbench.

## Architecture

```text
Agent + MCP/DuckDB run
         ↓
  Capture (run/tool/model IDs, costs, traces)
         ↓
  Golden case matching + grading
         ↓
  Human review ↔ Phoenix trace
         ↓
  Baseline comparison / regression report
```

Runs are matched to golden cases with explicit, reviewable mappings. Reference facts come from hand-authored SQL executed against the pinned taxi dataset—not from model-generated answers. Grader and human verdicts remain separate.

## Golden dataset

[37 versioned cases](../tooling/evals/datasets/golden/v1/cases.jsonl) cover five behaviors:

| Category | Cases | Example / purpose |
| --- | ---: | --- |
| **Deterministic** | 14 | Known taxi questions; expected tool arguments and SQL-backed facts |
| **ReAct investigations** | 8 | Multi-step analysis and caveats (e.g. cash tips are not recorded) |
| **Multi-turn** | 5 | “Busiest hours?” → “Break that hour down by borough”; carry context and filters |
| **Unsupported requests** | 6 | Missing driver IDs, unsupported forecasts, or deletion requests; **scope enforcement** and evidence-aware refusal |
| **Failure scenarios** | 4 | Invalid tool requests, timeouts, empty results, and provider failures |

Each case defines expected behavior and, where applicable, tool calls, reference facts, tolerances, and step limits. The dataset does not currently include a dedicated misspelling or prompt-injection suite.

## Graders

- **Deterministic checks:** tool choice/arguments, grounded facts, evidence presence, completeness, failure behavior, and step budgets.
- **LLM-as-judge:** optional DeepEval/GEval scores for qualitative dimensions, including faithfulness and task completion; recorded scores allow offline replay without live model calls.
- **Comparisons:** baseline vs candidate quality, latency, tokens, and cost, with versioned datasets and thresholds.

Automated scores are evidence for review, not substitutes for validated reference data.

## Human review workbench

The [review tool](../tooling/evals/src/evals/review_server.py) opens frozen runs, links to **Phoenix traces**, and records separate **tool, answer, and multi-turn resolution** verdicts. Reviews are append-only and bound to the evidence snapshot; reports highlight grader/human disagreements and incomplete reviews.

## Current limitations

All **37 golden cases await human approval**. Committed benchmark fixtures are **synthetic or scripted**, so their scores must not be presented as verified live model performance. Some cases use different SQL filtering bases and remain diagnostic until reconciled.

**Details:** [Experiment procedures and model comparisons](evaluation/running-evaluations.md) · [Capture format](capture.md) · [Golden dataset](../tooling/evals/datasets/golden/v1/cases.jsonl).
