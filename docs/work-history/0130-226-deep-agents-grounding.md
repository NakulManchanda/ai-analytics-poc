# 0130 — Deep Agents grounding parity (#226, parent #224)

## Goal
Apply the same deterministic numeric/evidence grounding check (#180) to Deep Agents answers that the manual loop applies, so correctness and cost-per-correct-answer comparisons in #228 are not skewed by one arm skipping the guard.

## Starting point
Deep Agents persisted the raw final answer and recorded `grounding: "not_applied"` (PR #237 review, P2). The manual loop wraps `check_answer_grounding` in a nested closure over a single tool result.

## Decisions
- `check_answer_grounding_multi(answer, evidences, question=)` in `validation/grounding.py`: a number is grounded if any evidence supports it; a cited query id is known if it matches any evidence's `query_id`; an empty list behaves like no evidence. `check_answer_grounding` now delegates to it with identical behavior (existing tests unchanged).
- The Deep Agents recorder collects the `query_result` of every successful tool call as evidence (failed calls never count). Before `answer.completed` and persistence, the multi check runs against the question; a caveat is appended exactly as in the manual loop and a `grounding_check` RunStep is recorded (failed if not ok, `caveat[:200]`, report metadata). The emitted, persisted and returned answers are the same grounded text.
- Run metadata: `grounding_report` {ok, ungrounded_numbers, unknown_query_ids, evidence_count} is present only when the check ran (completed path), and `grounding` is derived from it: `"applied"` if the report exists, `"not_applied"` for failed, cancelled and budget-exceeded runs that ended before the check (PR #239 review fix, with regression tests).
- `_execute_loop` and the manual/crewai paths are untouched.

## Verification
- `services/app`: 578 passed, 1 skipped (live test); new tests for the multi helper and for the Deep Agents run (supported/unsupported number, number supported by the first of two results, unknown cited id, no evidence with and without numbers, clarifying answer, failed tool not evidence); affected files repeated 10x without flakes; black and ruff clean.
- Not verified: behavior against live LiteLLM/Bedrock.

## Known limits
`grounding_report` is absent (and `grounding` is `not_applied`) on failed, cancelled and budget-exceeded runs. For #228: the multi-evidence check validates numbers and query ids independently across all evidence, so a number from query A can support an answer that attributes it to query B; it gives parity with the existing guard but does not establish claim-to-query faithfulness. Track query attribution and semantic faithfulness as evaluation cases. Model-policy and Phoenix/OTEL parity are the next slices (plan in the #226 thread).

## PR / merge state
Branch `feat/226-parity`; draft PR referencing #226.
