# 0100 — #180 structured validation and grounding

## Goal
Deterministic guardrails around the planner and the answer: semantic validation before any
governed tool runs, bounded validate-retry, numeric/evidence grounding, prompt-injection tests.

## Starting point
main at fb3e541. `parse_query_proposal` checked shape only; invalid proposals failed immediately.
Answers were unchecked against tool evidence.

## Decisions
- New `app/validation/` package; `loop.py` call sites only (Step C, pre-execution gate, tool-step
  observation scan, `apply_grounding`). No prompt wording or rendering changed (that is #178).
- One retry maximum, recoverable errors only, gated on LLM-call/token/time headroom for retry + answer.
- Out-of-scope time and a second governed analysis are terminal (unsupported, not fixable).
- Grounding failures append a fixed caveat and a `grounding_check` step rather than regenerating (no extra spend).
- Evals untouched; deterministic fabricated-number grading already exists there.

## Verification
`uv run --project services/app pytest services/app/tests`, black and ruff clean. Integration tests prove
malformed, unauthorized and out-of-scope proposals execute nothing, retry is bounded and budget-aware,
MCP denials are not retried, and hostile tool text cannot trigger a second tool call.

## PR / merge state
See the PR.

## Lessons
Keep feedback text template-only so hostile model output can never be re-injected into the next prompt.
