# 0106 — Reported cost with unknown usage is not authoritative (#187 follow-up)

## Goal
Close the P2 left by the #194 review: `call_cost` trusted any non-null `result.cost_usd`, even when `usage_known` was false. A client returning a partial estimate on incomplete usage must not be authoritative for budget accounting.

## Starting point
After #194, a reported cost bypassed the unknown-cost path entirely. Today's LiteLLM client only produces policy estimates when usage is known, so nothing triggered it; the guard is for future clients.

## Decisions
- A reported cost is authoritative when `cost_kind == "billed"` (independently billed) or when `usage_known` is not false. A non-billed estimate with `usage_known=False` falls through to the existing unknown-cost path: labelled `estimate_incomplete_usage`, charged the conservative bound, `cost_indeterminate=True`.
- No change to known-cost behaviour, the vocabulary, the monthly guard or pricing tables.

## Verification
- `uv run --project services/app pytest services/app/tests` -> 404 passed; `pytest tests` -> 93 passed, 1 skipped.
- `black --check services/app tests services/gateway` and `ruff check services/app` clean.
- New tests: estimate on unknown usage gets the conservative bound; it fails closed under a tight per-run limit; a billed cost on unknown usage stays authoritative. The two estimate tests failed before the change.
- Unverified: no live run; gateway and benchmark suites were not rerun (the change is confined to `orchestration/loop.py`).

## PR / merge state
See the PR for the #187 follow-up.

## Lessons
- "Known cost" and "known usage" are separate facts; trusting one does not establish the other.
