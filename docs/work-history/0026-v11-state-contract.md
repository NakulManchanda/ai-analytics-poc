# Work History Entry: 0026

## Goal

Deliver the v1.1 durable state and synchronous API contract tracked by GitHub issue #45.

## Starting Point

`/api/ask` maintained a second orchestration flow, used in-memory state even when the deployed DynamoDB table was configured, and returned null conversation/run IDs on an initial turn.

## Changes

- Select `DynamoDBStateRepository` only when `DYNAMODB_TABLE_NAME` is configured; retain `InMemoryStateRepository` for local development and tests.
- Make `/api/ask` a thin synchronous adapter over `OrchestrationLoop`.
- Generate conversation, run, LLM-call, and tool-call IDs in the application loop; persist user and assistant messages, runs, and steps.
- Add `GET /api/conversations/{conversation_id}` to reload ordered messages, runs, and run steps from the state repository.
- Add a conversation-run index item in the existing DynamoDB table shape so reload does not require a scan, GSI, Terraform change, or new AWS resource.
- Surface state repository failures rather than falling back to memory.

## Decisions

- The first `POST /api/ask` creates a backend-generated conversation; later turns may reference only an existing conversation ID.
- Keep blocking request/response behavior. Streaming, run-first execution, and v2 features remain out of scope.
- DynamoDB remains durable truth; Redis publishing is transient and may fail independently of the request.

## Verification

- Red/green TDD checks: repository selection, loop delegation, two-turn persistence/reload, visible persistence failure, and DynamoDB run ordering.
- `uv run pytest tests -q` from `services/app` — 82 passed.
- `uv run ruff check app tests` from `services/app` — passed.
- `uv run black --check app tests` from `services/app` — passed.
- `uv build` from `services/app` — source distribution and wheel built.
- `npm test -- --run && npm run build` from `web` — 10 tests passed and production build completed.

## PR and Merge State

- Branch: `codex/v11-state-contract`
- Tracking issue: #45
- Draft pull request: [#51](https://github.com/NakulManchanda/ai-analytics-poc/pull/51); not merged or deployed.

## Limitations

- Deployed DynamoDB/ECS verification is intentionally deferred to the integrated v1.1 checkpoint; this change adds no Terraform or AWS resources.
