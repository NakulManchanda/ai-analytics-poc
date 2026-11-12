# Agent Harness Architecture

The FastAPI application owns the **agent run lifecycle**: model calls, governed MCP tool execution, budgets, durable state, cancellation, and user-facing SSE events. The agent harness is distinct from the inference gateway, which schedules individual model requests.

## Execution

```text
Question → Conversation/run state → Agent planner
       → Allowed MCP tool → DuckDB evidence
       → Grounded answer → Save result + SSE timeline
```

| Strategy | Behavior |
| --- | --- |
| **Custom ReAct (default)** | Bounded plan → tool → observe → answer loop; deterministic query catalogue, validation, context reducer, cancellation and run budgets. |
| **Deep Agents (opt-in)** | LangGraph/Deep Agents tool orchestration wrapped in the application's run lifecycle; bounded restored history and recorded execution steps. |

Both strategies execute against governed tools, but **their prompt, history, and streaming behavior are not identical**. The manual path supports streamed answer deltas where the model client does; Deep Agents currently emits the completed answer rather than token deltas.

## Boundaries

- **Application:** run state, execution policy, MCP client, answer grounding, events.
- **MCP service:** safe structured analytical operations over DuckDB/Parquet.
- **Model client:** Bedrock through LiteLLM or self-hosted vLLM through the owned inference gateway.
- **Evaluation:** golden cases and captured evidence assess tool choice, answer quality, cost, and multi-turn resolution.

**Current gaps:** explicit multi-turn query resolution, strategy-equivalent prompt contracts, and controlled ReAct-versus-Deep-Agents benchmark results.

**Code:** [Orchestration](../services/app/app/orchestration/) · [MCP client](../services/app/app/mcp_client.py) · [Context engineering](architecture/context-engineering-and-evidence.md) · [Evaluation](evals.md) · [Inference architecture](../DESIGN.md).
