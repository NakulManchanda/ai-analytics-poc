# 0037 — Clarify Single-Tool Proposal Prompt and Support Empty Arguments

## Goal
Explicitly instruct Bedrock in `propose_taxi_query` to return exactly ONE tool call and omit `region_name` when comparing all boroughs across NYC. Also allow `proposal.arguments` to be `None` or empty mapping in `parse_query_proposal` for zero-argument tool calls.

## Starting Point
When prompted to compare all boroughs, Bedrock Nova Micro emitted 5 parallel `toolUse` blocks (one for each borough). `propose_taxi_query` rejected multiple tool blocks and returned empty name, causing a tool validation error.

## Decisions
- Updated user prompt in `propose_taxi_query` in `services/app/app/llm.py` to state: "Choose exactly ONE governed analysis that answers the question. Do not make multiple tool calls. To compare all boroughs across NYC, call average_trip_metrics with region_name omitted."
- Updated `parse_query_proposal` in `services/app/app/orchestration/loop.py` so `arguments = proposal.arguments or {}` allows `None` arguments for zero-argument tools.
- Added unit test in `services/app/tests/test_orchestration_loop.py`.

## Verification
- `uv run --project services/app pytest services/app/tests`: 99 passed.
- `make test`: all backend, MCP, dataset spike, infra, and frontend tests passed.
