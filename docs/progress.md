# Current milestone

Milestone 8 — bounded orchestration loop with execution budgets

## Status

IN PROGRESS — [issue #25](https://github.com/NakulManchanda/ai-analytics-poc/issues/25)

## Merged milestone baseline

- **Milestones 0–7**: Merged (`53e7928`), including governed `query_taxi_data` and DynamoDB state repository boundaries.
- **Milestone 13 Foundation**: Terraform infrastructure foundation and budget alerts merged.

## Acceptance criteria

- [x] Application-owned agent loop replaces hard-coded sequence (`while within budgets: context -> LLM -> tool/final`).
- [x] Configurable execution budgets enforced: max iterations, max LLM calls, max tool calls, deadline, token limits, cost budget, tool result byte limit.
- [x] Repeated-equivalent-tool-call detection implemented.
- [x] Terminal state `BUDGET_EXCEEDED` handled and recorded.
- [x] Durable state recorded across loop steps (`Conversation`, `Message`, `Run`, `RunStep`).
- [x] Full test suite passing across all services (`make test`).

## Next milestone

Do not start Milestone 9 (Redis Streams + SSE, #21) until Milestone 8 is merged.
