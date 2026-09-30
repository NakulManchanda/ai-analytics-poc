# Work history 0070 — Bounded CrewAI strategy behind application-owned LLM boundary (#139)

## Goal

Add `AGENT_STRATEGY: manual | crewai` configuration to test the "less code" multi-agent hypothesis
concretely. In `crewai` mode, a sequential 2-agent crew (researcher -> writer) answers governed
taxi questions through the already-governed tool result, while every model call flows through our
application-owned `GatewayLLM` adapter delegating to `LLMClient` — CrewAI never talks to models
directly, has no raw SQL access, and operates under strict execution budgets and cancellation.

## Starting point

- `AGENT_STRATEGY` was not present in `Settings` or `config.py`.
- No `crewai` dependency existed in `pyproject.toml`.
- Orchestration loop (`loop.py`) executed tool selection (catalogue or proposal model call) ->
  governed MCP execution -> durable tool observation -> context reduction -> single final answer
  call with budget tracking and durable state.
- `LLMClient` protocol owned all model interactions (`ServeLLMClient` and `BedrockLLMClient`).

## Decisions

- **D-139-1: Runtime compatibility on Python 3.12.** Unpinned Python 3.14 fails importing CrewAI
  via `chromadb`'s legacy pydantic compatibility layer. CI and local app runtime are pinned to
  Python 3.12 (`.python-version`).
- **D-139-2: Owned GatewayLLM adapter.** Rather than letting CrewAI call external providers or LiteLLM
  directly, a lightweight `GatewayLLM(BaseLLM)` delegates all calls to the application's `LLMClient.ask()`.
  Function calling is explicitly disabled on the adapter.
- **D-139-3: Explicit telemetry opt-out without global side-effects.** CrewAI telemetry is disabled via
  `CREWAI_DISABLE_TELEMETRY=true`. `OTEL_SDK_DISABLED` is avoided because it would disable the
  application's own OpenTelemetry tracing.
- **D-139-4: Hard invariant on call count.** Crew runs with `max_iter=1`, `memory=False`,
  `allow_delegation=False`, and asserts exactly 2 calls occurred (1 researcher, 1 writer). Any
  discrepancy raises an error rather than allowing unbounded agent loops.
- **D-139-5: Bounded user output.** Raw CrewAI agent backstory, task description, and reasoning
  chains are never exposed to user-facing fields; only the writer's final text is returned and persisted.

## What changed

- **`services/app/pyproject.toml` & `uv.lock`**: added `crewai>=1.15.23`. Added `.python-version` (3.12).
- **`services/app/app/config.py`**: added `agent_strategy: str = "manual"` to `Settings`, rejecting
  any strategy other than `"manual"` or `"crewai"` on startup.
- **`services/app/app/main.py`**: passed `resolved_settings.agent_strategy` into `OrchestrationLoop`.
- **`services/app/app/orchestration/crewai_strategy.py`**:
  - Implemented `GatewayLLM(BaseLLM)` with structured call observation callback.
  - Implemented `run_two_agent_answer` with bounded sequential 2-agent crew over governed query results.
- **`services/app/app/orchestration/loop.py`**:
  - `OrchestrationLoop.__init__` accepts `agent_strategy: str = "manual"`.
  - In `crewai` mode, executes `run_two_agent_answer` with live `on_call` hook recording budget and
    checking cancellation.
  - Records both model calls into `llm_calls` with standard telemetry fields (`input_tokens`,
    `output_tokens`, `cost_usd`, `latency_ms`, `finish_reason`).
  - Records `RunStep(step_type="crewai_crew", ...)` into durable state.
  - Emits `answer.completed` and `llm.completed`.
- **`services/app/tests/test_agent_strategy.py`**: comprehensive unit and end-to-end tests covering
  configuration validation, `GatewayLLM` delegation, two-agent output bounding, end-to-end loop
  execution, exact call count verification, telemetry parity, durable state, budget enforcement,
  and failure handling.
- **`services/app/tests/test_main.py`**: added test confirming `create_app()` passes `agent_strategy`.

## Code volume and execution comparison

| Dimension | Manual Strategy | CrewAI Strategy |
|---|---|---|
| **Answer-phase code volume** | ~140 lines in `loop.py` | ~250 lines (`crewai_strategy.py` + `loop.py` branch) |
| **Replaces `loop.py` foundation?** | No (is foundation) | No (relies entirely on `loop.py`'s ~1,200 lines for tool governance, MCP, state, budgets) |
| **Model calls in answer phase** | 1 call | 2 calls (researcher + writer) |
| **Answer-phase token usage** | 15 in / 8 out (~23 total) | 30 in / 16 out (~46 total) |
| **External dependencies** | 0 extra | ~50 transitive packages (crewai, litellm, chromadb, etc.) |

**Conclusion on "less code":** CrewAI did not reduce orchestration code. Caging it inside safety,
budget, and governance boundaries required ~250 lines of adapter logic, while executing twice the
LLM calls in the answer phase. However, it provides a clean, bounded sequential generator that can
be leveraged for growing-prefix testing in future slices.

## Verification

- `source .venv/bin/activate && pytest` -> 212 passed, 0 failures.
- `ruff check app/ tests/` -> all checks passed.
- `git diff --check` -> clean, no whitespace or formatting errors.
