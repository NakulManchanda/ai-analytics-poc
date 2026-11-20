# Agent UX Experiments: Streaming Specialists, Typed Artifacts & Spatial Workbench

This document records the architecture, contracts, security boundaries, and evaluation outcomes for **Epic #231** (`#232`, `#233`, `#234`, `#235`, `#236`). All components are opt-in, fixture-backed vertical slices that reuse the existing FastAPI / Redis / SSE / React stack without altering the production single-agent ReAct loop.

## 1. Architecture & Contracts

### 1.1 Structured Agent/Subagent Events (`agent_event.v1` — `#232`)
- **Implementation:** `services/app/app/events/agent_events.py` (Python producer/validator) and `web/src/workbench/state.ts` (TypeScript reducer).
- **Schema:** Every event carries `schema_version="agent_event.v1"`, monotonic `sequence`, ISO-8601 `timestamp`, `trace_id`, `run_id`, `tenant_id`, `agent_id`, `parent_agent_id`, `event_type`, optional correlation IDs (`tool_call_id`, `artifact_id`), `payload`, and nullable `usage`.
- **Transport:** Bridges onto the existing `RunEvent` / `EventPublisher` SSE envelope (`to_run_event()` / `from_run_event()`), so no new streaming transport is required.
- **Reliability:**
  - `replay_deduplicated(events, last_sequence, tenant_id)` enforces tenant isolation, orders by `sequence`, and drops duplicates on reconnect.
  - `AgentEventEmitter.cancel()` emits `run_cancelled` and suppresses subsequent non-cancel events.

### 1.2 Typed Artifact Envelope & Cross-Agent Synthesis (`artifact_envelope.v1` — `#235`)
- **Implementation:** `services/app/app/artifacts/envelope.py` and `web/src/artifacts/envelope.ts` + `web/src/artifacts/ArtifactPanel.tsx`.
- **Supported kinds:** `table`, `time_series`, `segment_comparison`, and `map` (delegating to the v1 `map_artifact` schema and `ZoneMap` renderer from `#234`).
- **Deterministic builders:** Artifact bodies are derived directly from validated tool results (`table_from_tool_result`, `time_series_from_tool_result`, `segment_comparison_from_tool_result`, `map_envelope_from_artifact`). Both backend and frontend reject mismatched `total_y` sums and mismatched `delta = comparison - baseline` values.
- **Cross-agent synthesis (`synthesize_artifacts` / `synthesizeArtifacts`):** Flags `basis_mismatch`, `time_window_mismatch`, `truncated_source`, and `value_contradiction` across artifacts emitted by different specialists before synthesis.

### 1.3 Live Specialist Workbench UI (`#233`)
- **Route:** Opt-in `/?demo=workbench` (`web/src/workbench/WorkbenchDemo.tsx` and `SpecialistWorkbench.tsx`), keeping `LiveApp` untouched.
- **Capabilities:** Concurrent specialist cards with truthful lifecycle states (`queued`, `running`, `completed`, `failed`, `cancelled`), expandable tool-call traces (including failed attempts + retries), replay/reconnect simulator proving zero duplicate cards, mid-flight cancellation, and a side-by-side **Single-Agent Baseline** toggle.

## 2. Threat Model & Guardrails

| Risk | Mitigation | Verified In |
|---|---|---|
| Raw chain-of-thought or system prompt leakage | Closed blocklist (`chain_of_thought`, `raw_cot`, `hidden_reasoning`, `system_prompt`, `prompt_template`) rejected at event validation | `test_agent_events.py` |
| Credential / secret leakage in event payloads | Regex guard rejecting `AKIA...`, `sk-...`, `Bearer ...`, `-----BEGIN ... PRIVATE KEY-----` | `test_agent_events.py` |
| Fabricated progress percentages or fake usage | Rejects `percent_complete` / `progress_pct` / `eta_seconds`; `usage` fields are strictly nullable and rendered as `Unavailable` when absent | `test_agent_events.py`, `SpecialistWorkbench.tsx` |
| Oversized stream frames or artifacts | 16 KiB cap on `agent_event.v1`; 96 KiB cap on `artifact_envelope.v1` | `test_agent_events.py`, `test_artifact_envelope.py`, `ArtifactPanel.test.tsx` |
| XSS / unsafe markup in rendered artifacts | Rejects `<script`, `javascript:`, `onerror=`, `onload=`, `<iframe`, `<svg` in envelope strings; React renders text nodes only | `test_artifact_envelope.py`, `ArtifactPanel.test.tsx` |
| Cross-tenant replay leakage | `replay_deduplicated` filters strictly by `tenant_id` | `test_agent_events.py` |

## 3. Experiment Scenarios & Measurable Rubric (`#236`)

All scenarios are encoded as reproducible fixtures in `web/src/workbench/experiments.ts` and verified in `web/src/workbench/experiments.test.ts`:

| Scenario | Evidence & Artifacts | Groundedness / Synthesis Result | UX Finding |
|---|---|---|---|
| **1. Busiest Pickup Zones by Borough** | `table` + Queens `map` | Pass — aligned `governed_valid_records` basis, zero contradictions | Spatial map + ranked borough table makes Manhattan volume vs Queens airport share immediately scannable. |
| **2. 2023 vs 2024 Boundary Check** | None (explicit boundary notice) | Pass — refuses to invent 2023 figures; zero fabricated charts | Explicit dataset boundary in specialist summary builds trust better than empty or guessed charts. |
| **3. Airport Anomaly Drill-Down** | Queens `map` + `time_series` + `segment_comparison` | Pass — transient tool timeout retried cleanly; synthesis aligned | Parallel specialist cards clarify *why* a multi-step query takes longer and expose retry recovery transparently. |
| **4. Weekday vs Weekend Linked Filter** | `segment_comparison` + `time_series` | Pass — deltas verified (`+9.97%` Manhattan, `+5.0%` Queens) | Delta table + provenance header eliminates ambiguity around filter basis and time window. |
| **Control: Invalid Date / Empty Data** | None (`tool_failed` → `run_failed`) | Pass — fails closed with explicit error | Clear failure state prevents silent fallback or hallucinated totals. |

## 4. Ship / Iterate / Reject Recommendations

- **Ship (ready to wire into live flows when needed):**
  - `artifact_envelope.v1` + `ArtifactPanel` (`#235`) and `ZoneMap` (`#234`): deterministic, schema-validated, XSS-safe, and immediately useful even for the single-agent ReAct loop.
  - `synthesize_artifacts` guardrails: cheap deterministic check that prevents silent basis/time-window mismatches.
- **Iterate (keep opt-in behind `/?demo=workbench` until multi-agent execution justifies cost):**
  - `agent_event.v1` + `SpecialistWorkbench` (`#232`, `#233`): clean contract and UI, but because the Deep Agents multi-agent benchmark (`#225`–`#230`) was deprioritized after single-agent ReAct outperformed it on NYC Taxi, keep specialist orchestration opt-in rather than replacing the default single-agent loop.
- **Reject:**
  - Fabricated progress percentages, raw chain-of-thought streaming, and unvalidated LLM-authored chart/HTML payloads — explicitly prohibited by schema validators.

## 5. Manual Demo

```bash
cd web && npm run dev
# Open http://localhost:5173/?demo=workbench
```
Use the scenario bar at the top to switch between the 4 NYC Taxi experiments, the mid-flight cancellation stream, and the invalid-date control, or toggle **Compare: Single-Agent Baseline**.
