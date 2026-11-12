# Golden dataset and scope coverage

[Evaluation documentation index](../evals.md)

## Dataset v1 (37 cases)

14 deterministic catalogue questions, 8 ReAct investigations, 5 multi-turn conversations, 6
unsupported/causal requests, 4 failure scenarios (tool validation error, timeout, empty result,
provider error). Every `review.status` is `pending_human_review` until the PR reviewer promotes it.
Facts use tolerances (counts 0.1%, averages 1%) and accept `23.8%` for `0.2376`.


## Golden dataset: why these cases were chosen

The [versioned dataset](../../tooling/evals/datasets/golden/v1/cases.jsonl) is an **agent-behavior benchmark for governed SQL analytics**, not a general-purpose document QA dataset. Each case records the user question (and follow-ups), expected behavior, allowed/expected tools and arguments, step limits, and—where relevant—reference SQL with expected facts. The reference SQL is hand-authored and the numerical facts are refreshed against pinned Parquet data; candidate model output is never automatically promoted to ground truth.

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

