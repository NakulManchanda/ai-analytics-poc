# Captured session report

- origin: **local-scripted** - NOT LIVE: the model provider and dataset server were scripted; tokens, cost, latency and answers are not live Bedrock measurements. No live Bedrock run backs these numbers.
- source: `taxi-session.jsonl` (capture.v1), runs: 2, conversations: 1
- captured traffic is never golden; a graded comparison needs an explicit match to a human-reviewed golden case.
- **PROVISIONAL**: graded against golden references that are still `pending_human_review`. Correctness below is not an approved result.

## Turns

| # | question | linked golden turn | grading | tools (actual) | tools expected |
|---|---|---|---|---|---|
| 0 | Which pickup zones have the most trips? | `multi-001#0` | provisional PASS: tool_arguments=pass, step_budget=pass, numeric_facts=pass, evidence_present=pass | query_taxi_data({"analysis": "top_pickup_zones", "limit": 5}) completed rows=2 | query_taxi_data |
| 1 | Compare that with the second highest zone | `multi-001#1` | provisional PASS: tool_arguments=pass, step_budget=pass, numeric_facts=pass, evidence_present=pass | compare_taxi_segments({"baseline_filters": {"pickup_zone": "JFK Airport"}, "comparison_filters": {"pickup_zone": "Upper East Side South"}, "limit": 5, "measures": ["trip_count"], "segment_dimension": "pickup_zone"}) completed rows=2 | compare_taxi_segments |

- turn 0 answer: JFK Airport has the most pickups with 145240 trips.
- turn 1 answer: Upper East Side South is second with 141266 trips, about 3.9 thousand fewer than JFK Airport.

## Tokens, cost, latency

| # | model calls | aliases | input | output | cost USD | model ms | tool ms |
|---|---|---|---|---|---|---|---|
| 0 | 1 | taxi-answer | 412 | 38 | 0.000020 | 35 | 0 |
| 1 | 2 | taxi-answer, taxi-planner | 1158 | 102 | 0.000055 | 66 | 0 |
| total | | | 1570 | 140 | 0.000075 | 101 | 0 |

Totals are sums of known values only. `n/a` means the provider reported no usage; it is never counted as 0.
Pricing versions: 2026-10-bedrock-nova-micro-v1; cost kinds: estimated; fallbacks used: 0.
Judge metrics: not run on captured traffic (no recorded judge verdicts exist for it).
