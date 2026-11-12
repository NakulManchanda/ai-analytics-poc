# Context Engineering & Evidence Retrieval

The NYC Taxi assistant grounds answers in **structured SQL evidence** retrieved through governed MCP tools. It does not require document chunking or vector retrieval for its current analytical workload.

## Request flow

```text
Question → Tool catalogue / LLM planner → Validated MCP call
         → DuckDB / Parquet SQL → Tool observation
         → Context reduction + answer generation
         → Grounding checks → Persist answer / SSE events
```

- **Tool selection:** known questions use an exact-match catalogue; otherwise the model proposes an allowlisted tool and validated arguments.
- **Evidence:** SQL results include rows, columns, counts, and query identifiers. Tool observations are persisted for subsequent inspection.
- **Context reducer:** retains the current question, recent messages, short previews of older messages, schema, budgets, and at most **three preview rows per current observation** plus an artifact reference.
- **Grounding:** checks numeric claims and query identifiers against the underlying tool observation; flags unsupported causal claims.

**Important distinction:** `WorkingContext` is recorded for evaluation and observability, but the model clients assemble their actual prompt separately. The reduced SQL preview is **not guaranteed to be the context the answer model consumes**.

## Follow-up context by runtime

| Runtime | Model receives for Q2 |
| --- | --- |
| **Manual ReAct + LiteLLM/Bedrock** | Q2 with schema/tool instructions, followed by Q2's current SQL result for answering; stored Q1/A1 are not passed as model history. |
| **Manual ReAct + owned vLLM** | System instructions, stored Q1, prior tool observation, A1, then Q2; history rendering is not currently bounded. |
| **Deep Agents** | Up to four earlier turns with bounded answers and selected tool observations, followed by Q2; prior tool content is marked untrusted. |

The model receives a **conversation ID** for correlation where supported; that does not itself restore conversational history.

## Current limitations

**Broader query routing and explicit multi-turn query resolution are pending.** Bounded context selection and prompt parity across all model paths also need evaluation. Test follow-ups such as “compare that with second place,” plus full vs reduced SQL evidence for accuracy, token use, and cost.

**Code:** [Orchestration](../../services/app/app/orchestration/loop.py) · [MCP client](../../services/app/app/mcp_client.py) · [ContextReducer](../../services/app/app/orchestration/reducer.py) · [Conversation renderer](../../services/app/app/prefix_render.py) · [Grounding](../../services/app/app/validation/grounding.py).
