# 0038 — Resolve Multi-Borough Tool Uses to All-Borough average_trip_metrics Call

## Goal
When asked to compare boroughs, Bedrock Nova Micro generates multiple parallel `average_trip_metrics` toolUse blocks (one for each borough in the enum). Resolve these multiple regional calls to a single all-borough `average_trip_metrics(arguments={})` tool call.

## Starting Point
`propose_taxi_query` previously rejected any response where `len(tool_uses) != 1`. When Nova Micro generated 5 `average_trip_metrics` calls (one for each borough in the borough enum), it caused `name=""`, triggering a tool validation failure.

## Decisions
- Updated `propose_taxi_query` in `services/app/app/llm.py` so that when `len(tool_uses) > 1` and all uses are `average_trip_metrics`, it resolves to `name="average_trip_metrics"` with `arguments={}`.
- Added unit test `test_bedrock_propose_taxi_query_resolves_multiple_borough_tools_to_all_boroughs` in `services/app/tests/test_ask.py`.

## Verification
- `uv run --project services/app pytest services/app/tests`: 100 passed.
- `make test`: full backend, MCP, dataset spike, infra, and frontend suites passed.
