# Context Engineering & Evidence Retrieval — Current Implementation

> **Status:** As-built description of the NYC Taxi analytics application. This document explains current runtime behavior, not a generic document-RAG reference design. Research and potential alternatives remain in [docs/research/context/](../research/context/).

## Purpose and boundaries

Users ask analytical questions about NYC Taxi data. The application maps each question to a governed MCP analytical operation, executes SQL through DuckDB against Parquet data, and uses returned evidence to generate a grounded answer. This is **tool-grounded structured-data retrieval**, not a chunk/vector document index. Indexing, BM25, embeddings, and reranking are **not requirements** for this workload today.

**Code owners:** [orchestration loop](../../services/app/app/orchestration/loop.py) · [MCP client](../../services/app/app/mcp_client.py) · [query catalogue](../../services/app/app/query_catalogue.py) · [context reducer](../../services/app/app/orchestration/reducer.py) · [grounding checks](../../services/app/app/validation/grounding.py).

## Request and evidence flow

```text
User Qn + conversation_id
    → persist user question and run
    → load dataset schema from MCP
    → exact catalogue lookup OR model proposal of allowlisted tool + arguments
    → validate arguments and execute MCP/DuckDB SQL
    → persist tool observation (query_id, rows, metadata)
    → build reduced WorkingContext for run evidence
    → model answer grounded in SQL result
    → check numeric/query-id claims against tool observation
    → persist assistant answer and publish SSE execution events
```

The catalogue skips the planning model for known questions. It is an **exact normalized-question routing optimization**, not semantic retrieval. A model-proposed tool call is checked against a bounded tool contract before execution.

## What the ContextReducer actually does

`ContextReducer.reduce()` produces a `WorkingContext` containing:
- current user message, **up to four recent historical messages** (the default two-turn setting counts messages, not reconstructed dialogue pairs), and a deterministic preview summary of older messages (up to 60 characters per message);
- available tools, dataset schema, assumptions, failures, and remaining execution/token/cost budgets;
- current tool observations summarized to **columns, row count, at most three preview rows, query id, execution time, and an artifact reference**.

The reduced context is recorded in `context.reduced` events and run-step metadata. **Important:** the model clients build answer prompts separately, often from the original `query_result`, so the existence of `WorkingContext` does **not** establish that the final model prompt always uses its reduced observation. That is a concrete area for an end-to-end prompt-contract test, not a claim to hide.

## Follow-up questions: what is sent to the model?

Durable history is stored by conversation ID, including user messages, tool observations, and assistant answers. **The model payload depends on the selected runtime path:**

| Runtime | Second-turn model context |
| --- | --- |
| **Manual ReAct + LiteLLM/Bedrock** | A new single-turn tool proposal over **Q2 and schema**; the answer call gets **Q2 and current SQL result**. `conversation_id` is supplied for correlation, but stored Q1/tool/A1 history is **not** included in the model message payload on this path. |
| **Manual ReAct + owned vLLM gateway** | `prefix_render.render_conversation_prompt` constructs **system + stored Q1 + prior tool observation + A1 + Q2**, excluding Q2 from stored history before appending it once. This path currently has **no bounded history selection in that renderer**. |
| **Opt-in Deep Agents** | Restores **up to four previous user/assistant turns** plus up to **two prior tool observations per turn**, then Q2. Caps: **12,000 characters total**; previous user text 1,000, assistant answer 1,500, each tool result 2,000. Observations are embedded as marked *untrusted tool data*, not free-standing tool-role messages. |

The manual history renderer lives in [prefix_render.py](../../services/app/app/prefix_render.py); Deep Agents uses [build_history_messages](../../services/app/app/orchestration/deep_agents_run.py). Both should be treated as independently versioned prompt contracts.

**Example:** Q1 “Which zone has the most pickups?” → tool returns ranked counts → A1 cites top zone → Q2 “What about second place?” A reliable planner should resolve “second place” against the prior analytical intent/evidence. Stored history alone does not guarantee the follow-up is correctly resolved.

## Existing safeguards and evidence

- **MCP/tool validation:** tool allowlists, argument validation, result sanitization, bounded operations.
- **Answer grounding:** checks numeric claims and query identifiers against tool observations; unsupported causal claims are flagged.
- **Observability:** run events, `context.reduced` data, token/cost/run telemetry, and OpenTelemetry/Phoenix traces.
- **Tests:** [reducer tests](../../services/app/tests/test_context_reducer.py), [tool tests](../../services/app/tests/test_governed_mcp_client.py), [grounding tests](../../services/app/tests/test_validation_grounding.py), and [Deep Agents history tests](../../services/app/tests/test_deep_agents_history.py).

## Open implementation gaps / experiments

| Area | Status / measurement |
| --- | --- |
| **Query routing** | Exact catalogue and model tool-selection implemented; broader intent-aware routing is **pending**. Compare selection accuracy, cost, fallback and latency. |
| **Multi-turn query resolution** | Some contextual history paths exist; an explicit, independently observable **query-resolution/rewrite stage is pending**. Evaluate ambiguous follow-ups such as “what about second place?” |
| **Prompt/context parity** | **Pending**: verify the actual model prompt consumes selected/reduced evidence consistently across manual Bedrock, owned vLLM, and Deep Agents. |
| **Memory selection** | **Pending experiment**: full history vs bounded recent window vs relevant historical observations, measured on correctness, tokens, and latency. |
| **SQL observation reduction** | **Pending benchmark**: full SQL result vs three-row preview vs tailored aggregate; score evidence preservation, grounded answers, and cost. |

Do not conflate **document retrieval** with **structured query execution**. Adopt document chunking, vectors, or reranking only when a real corpus and a measurable retrieval failure justify them. Broader alternatives live in [research notes](../research/context/README.md).
