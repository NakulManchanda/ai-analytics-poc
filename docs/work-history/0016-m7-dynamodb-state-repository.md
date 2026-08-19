# Work 0016 — Milestone 7 DynamoDB durable-state repository

## Goal

Add the application-owned DynamoDB durable-state boundary so `Conversation`, `Message`, `Run`, and `RunStep`
survive a process restart, independently of orchestration loops and UI work.

## Starting state

`origin/main` at `460123c` has Milestones 0–6 merged. The application can query taxi data through a fixed
governed flow, but maintains no persistent conversation or execution history.

## Decisions

- Introduce explicit domain dataclasses for `Conversation`, `Message`, `Run`, and `RunStep` with distinct prefixed IDs (`conv_`, `msg_`, `run_`, `step_`, `call_`, `tcall_`, `qry_`).
- Implement `DynamoDBStateRepository` mapping entities to single-table keys (`pk`, `sk`) on the provisioned `aws_dynamodb_table.application_state`:
  - `Conversation`: `pk = "CONV#<id>"`, `sk = "METADATA"`
  - `Message`: `pk = "CONV#<id>"`, `sk = "MSG#<sequence:06d>#<id>"`
  - `Run`: `pk = "RUN#<id>"`, `sk = "METADATA"`
  - `RunStep`: `pk = "RUN#<id>"`, `sk = "STEP#<sequence:06d>#<id>"`
- Provide `InMemoryStateRepository` reference implementation for unit testing.
- Enforce conditional writes (`attribute_not_exists(pk)` / `attribute_not_exists(sk)`) to prevent duplicate entities and race conditions.
- Handle Decimal/float conversions cleanly for token counters and estimated cost metrics.
- Keep domain entities and repository interfaces isolated in `app/state/` without premature HTTP endpoint wiring or loop logic (reserved for M8).

## Verification

- `make test` passed with 74 tests across all service suites:
  - `test_state_repository.py` validates domain models, ID generators, and `InMemoryStateRepository`.
  - `test_dynamodb_state.py` validates `DynamoDBStateRepository` CRUD, condition checks, sequence ordering, and simulated process restart survival across repository instances.
- Black formatting and Ruff linting checks passed cleanly.
- Python bytecode compilation passed with zero errors.

## Pull request and merge state

Branch `feat/dynamodb-state-repository` tracks [issue #18](https://github.com/NakulManchanda/ai-analytics-poc/issues/18).

## Lessons

- Single-table partition/sort key layout with zero-padded sequences (`MSG#000001#...`) provides natural ascending chronological ordering in DynamoDB range queries.
- Keeping state repository interfaces decoupled from FastAPI endpoint handlers ensures durable storage can be tested and verified before orchestration loops are layered on top.
