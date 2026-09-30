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
  directly, a lightweight `GatewayLLM(BaseLLM)` delegates all calls to the application's `LLMClient.ask()`
  or partitioned invocation callable. Function calling is explicitly disabled on the adapter.
- **D-139-3: Explicit telemetry opt-out without global side-effects.** CrewAI telemetry is disabled via
  `CREWAI_DISABLE_TELEMETRY=true`. `OTEL_SDK_DISABLED` is avoided because it would disable the
  application's own OpenTelemetry tracing.
- **D-139-4: Hard invariant on call count.** Crew runs with `max_iter=1`, `max_retry_limit=0`, `memory=False`,
  `allow_delegation=False`, and asserts exactly 2 calls occurred (1 researcher, 1 writer). Any
  discrepancy raises an error rather than allowing unbounded agent loops.
- **D-139-5: Bounded user output.** Raw CrewAI agent backstory, task description, and reasoning
  chains are never exposed to user-facing fields; only the writer's final text is returned and persisted.
- **D-139-6: Correlated growing-prefix contract.** CrewAI model calls are partitioned using `PrefixPartition`.
  Researcher (step 2) partitions on `Observation (query_result): ...`. Writer (step 3) partitions on
  `Observation (query_result): ... \n\n Research summary: ...`, extending the cached prefix. Headers
  pass `x-conversation-id`, `x-agent-step`, and `x-prefix-id`.
- **D-139-7: Pre-call enforcement and telemetry parity.** Check cancellation and execution budget limits
  *before* invoking the model, preventing over-budget or cancelled blocking provider calls. Pre-allocate
  `call_id`, emit `llm.started` and `llm.completed` per physical call, and persist `crewai_researcher`
  and `crewai_writer` steps immediately so partial failures retain completed-call telemetry.
- **D-139-8: Durable failure handling.** Convert `LLMProviderError` and `LLMConfigurationError` to
  `OrchestrationError` so the loop's outer exception handler durably transitions runs to `status="failed"`,
  records failure code, emits `run.failed`, and persists metrics.

## What changed

- **`docker-compose.yml` & `docker-compose.aws.yml`**: added `AGENT_STRATEGY: "${AGENT_STRATEGY:-manual}"`.
- **`infra/terraform/ecs.tf`**: added `AGENT_STRATEGY = "manual"` default environment variable.
- **`services/app/pyproject.toml` & `uv.lock`**: added `crewai>=1.15.23`. Added `.python-version` (3.12).
- **`services/app/app/config.py`**: added `agent_strategy: str = "manual"` to `Settings`, rejecting
  any strategy other than `"manual"` or `"crewai"` on startup.
- **`services/app/app/main.py`**: passed `resolved_settings.agent_strategy` into `OrchestrationLoop`.
- **`services/app/app/llm.py`**:
  - Added `execute_partitioned_call` to `LLMClient` protocol, `LocalFakeLLMClient`, `BedrockLLMClient`,
    and `ServeLLMClient`.
  - In `ServeLLMClient`, sends `x-conversation-id`, `x-agent-step`, and `x-prefix-id` headers.
  - In `BedrockLLMClient`, accepts optional `system` prompt and `max_tokens` for partitioned calls.
- **`services/app/app/orchestration/crewai_strategy.py`**:
  - Implemented `GatewayLLM(BaseLLM)` with custom invocation hook support and role propagation.
  - Implemented `run_two_agent_answer` with correlated growing prefix partitions and `max_retry_limit=0`.
- **`services/app/app/orchestration/loop.py`**:
  - In `crewai` mode, executes `run_two_agent_answer` via an internal `_crew_invoke_model` callable.
  - Pre-call check validates cancellation and `tracker.llm_call_count < tracker.budgets.max_llm_calls`.
  - Emits `llm.started` and `llm.completed` per physical call.
  - Persists `RunStep(step_type="crewai_researcher")` and `RunStep(step_type="crewai_writer")` immediately.
  - Catches `LLMProviderError` and `LLMConfigurationError` and maps to `OrchestrationError` to guarantee
    durable `failed` status and `run.failed` event emission.
- **`services/app/tests/test_agent_strategy.py`**: comprehensive unit and end-to-end tests covering:
  - Strategy configuration (`manual` default, `crewai` opt-in, invalid rejection).
  - `GatewayLLM` delegation and tool rejection.
  - Bounded output and absence of leaked agent backstory.
  - Correlated growing prefix partitions between researcher and writer.
  - `ServeLLMClient` request headers (`x-conversation-id`, `x-agent-step`, `x-prefix-id`).
  - End-to-end loop execution with per-call step types and telemetry.
  - Pre-call budget limit prevention.
  - Pre-call cancellation prevention.
  - Durable failure handling on provider errors.
  - Strategy execution failure handling.
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
LLM calls in the answer phase. However, it provides a clean, bounded sequential generator with
verified growing-prefix reuse.

## Verification

- `source .venv/bin/activate && pytest` -> 216 passed, 0 failures.
- `source .venv/bin/activate && black --check app tests` -> all checks passed.
- `git diff --check` -> clean, no whitespace or formatting errors.
