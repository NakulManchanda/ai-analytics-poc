# Captured runs and session reports

[Evaluation documentation index](../evals.md)

## Captured traffic: candidate -> reviewed golden -> comparison (#176)

Real `capture.v1` JSONL has `run_id`/`turn_id`/`call_id`/`conversation_id` but no `case_id`/`turn_index`.

1. `evals candidates --traces captured.jsonl` (needs question text, i.e. a capture made with `CAPTURE_CONTENT=1`; metadata-only captures have no text to match and must be linked by a human-written matches file) lists each run with *suggested* golden turns (exact question match; may be several). A suggestion is not a label.
2. A human writes a matches file (`evals.matches.v1`: `run_id`, `case_id`, `turn_index`, `confirmed_by`). Validation rejects unknown cases, bad turn indexes, a missing `confirmed_by`, and duplicate runs or targets.
3. `resolve()` grades a matched run only if the golden case is `review.status == "reviewed"`. Runs that are unmatched, or matched to a still-pending case, stay *candidate only* and are not graded. `--provisional` grades against pending references and the report is then marked PROVISIONAL.
4. `evals session-report --traces ... --matches ... [--conversation-id ID] [--provisional]` writes the per-session report: answer correctness, tool behavior (actual vs expected), tokens, cost, latency. LIVE is derived only from the `execution_mode` recorded in the capture and requires every record to say `live`; anything else (scripted, unverified, missing) prints NOT LIVE. `--origin live` is only a cross-check and is refused when the capture is not recorded as live. The file must hold exactly one conversation, or select one with `--conversation-id`; otherwise the command errors. Unknown tokens/cost render `n/a` and totals are marked as lower bounds.

`build_turns()` no longer raises on unlinked records; it skips them (use `build_run_views()` for raw capture). Tool status `completed` (the app) and `ok` (synthetic) both count as success.

`fixtures/captured/taxi-session.jsonl` is a two-turn session (`multi-001`) recorded by `services/app/tests/capture_session_fixture.py`: real `OrchestrationLoop`, `LiteLLMClient`, model policy/pricing and capture recorder; scripted provider (httpx mock) and scripted MCP. Answers, token counts and latency are scripted, so correctness passing is by construction; it proves the pipeline, not model quality. `matches.json` there is fixture wiring by the agent, not a golden review. `report.md` is the PROVISIONAL report for it. Replace with a live Bedrock capture (same commands; it reports LIVE only if the capture was recorded with `CAPTURE_EXECUTION_MODE=live`, which is an operator assertion made at capture time) once a run is available.

