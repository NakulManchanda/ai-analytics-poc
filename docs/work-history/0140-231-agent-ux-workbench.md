# 0140 — Epic #231 Agent UX Workbench (`#232`, `#235`, `#233`, `#236`)

- **Issues:** `#231` (Epic), `#232`, `#233`, `#235`, `#236` (`#234` previously merged in PR `#250`)
- **Branch:** `feat/231-agent-ux-workbench` (4 atomic commits, one per issue)

## Goal

Deliver the remaining vertical slices of Epic `#231` as clean, opt-in, fixture-backed capabilities reusing the existing FastAPI / Redis / SSE / React stack without modifying the production single-agent ReAct loop:
1. `#232` — `agent_event.v1` structured streaming event contract, emitter, privacy/size/progress guardrails, cancellation suppression, and tenant-scoped replay deduplication.
2. `#235` — `artifact_envelope.v1` typed artifacts (`table`, `time_series`, `segment_comparison`, `map`), deterministic builders, cross-agent synthesis contradiction checks, and React `ArtifactPanel` + `SynthesisBanner`.
3. `#233` — Opt-in `SpecialistWorkbench` UI mounted at `/?demo=workbench` with concurrent specialist cards, expandable tool traces + retry visibility, reconnect dedupe simulator, mid-flight cancellation, and single-agent baseline comparison.
4. `#236` — Reproducible NYC Taxi experiment scenarios (including pinned-dataset 2023 vs 2024 boundary check and invalid-date control) and UX findings report (`docs/agent-ux-experiments.md`).

## Key Decisions

- **Keep `LiveApp` untouched:** Mounted `WorkbenchDemo` at `/?demo=workbench` alongside existing `/?demo=map`.
- **Reuse existing contracts:** `agent_event.v1` bridges onto `RunEvent`/`EventPublisher` SSE framing; `artifact_envelope.v1` `map` kind delegates to `validate_map_artifact` and `ZoneMap` from `#234`.
- **Gitignore fix:** Added `!web/src/artifacts/` alongside `!services/app/app/artifacts/` so frontend artifact modules are tracked cleanly.
- **Map alias serialization:** Used `model_dump(mode="json", by_alias=True)` in `map_envelope_from_artifact` so the `schema` alias roundtrips cleanly through `validate_map_artifact`.

## Verification

- Backend (`services/app`): `uv run --project services/app ruff check services/app`, `black --check services/app`, and `pytest services/app/tests/test_agent_events.py services/app/tests/test_artifact_envelope.py` — all passing.
- Frontend (`web`): `npm test` (Vitest) and `npm run build` (`tsc --noEmit && vite build`) — all passing.
- `git diff --check` clean; no secrets or developer-specific absolute paths committed.

## PR & Merge State

- Opened draft PR for external review; CI verified green on exact head commit.
