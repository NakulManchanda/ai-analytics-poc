# Current Context System

## Summary

The current POC implements **deterministic context reduction**, not retrieval-augmented generation and not query rewriting.

## Current request path

```text
POST /api/ask
    │
    │ raw prompt
    ▼
OrchestrationLoop.run()
    │
    ├── persist user message/run
    ├── load dataset schema from MCP
    │
    ├── llm.propose_taxi_query(raw_prompt, schema)
    │       ↓
    │   validated MCP tool proposal
    │
    ├── execute MCP query
    │       ↓
    │   query_result
    │
    ├── ContextReducer.reduce(...)
    │       ↓
    │   WorkingContext
    │
    └── final answer LLM call
```

## `/api/ask`

`services/app/app/routers/ask.py` currently performs API-boundary responsibilities:

- validates that `prompt` is non-empty and at most 4,000 characters;
- accepts an optional `conversation_id`;
- forwards `request.prompt` unchanged to `orchestration_loop.run(...)`;
- maps orchestration errors into HTTP responses;
- shapes the final API response.

It does **not** currently perform query rewriting.

## Orchestration loop

`services/app/app/orchestration/loop.py` currently passes the original `prompt` directly to:

```python
llm.propose_taxi_query(prompt, schema)
```

Therefore the proposal LLM itself may understand conversational language, but there is no explicit, inspectable query-rewrite stage before retrieval/tool selection.

The current system also has a constrained tool vocabulary. The proposal LLM maps the natural-language request into an allowlisted analytical tool and arguments. That is closer to **intent/tool translation** than a reusable retrieval query rewrite.

## ContextReducer

`services/app/app/orchestration/reducer.py` currently:

1. separates the current message from historical messages;
2. keeps a fixed sliding window of recent turns;
3. deterministically compresses older messages into short previews;
4. includes the dataset schema and measures its serialized size;
5. reduces tool results to columns, row count, up to three preview rows, execution metadata, and an artifact reference;
6. exposes assumptions/failures;
7. exposes remaining execution budgets.

The current reducer does **not**:

- rewrite the user query;
- retrieve semantically relevant older conversation turns;
- perform BM25/keyword search;
- perform embedding/vector search;
- apply hybrid search;
- run a re-ranker;
- selectively retrieve schema elements by relevance.

## Important observation

The reducer is currently invoked **after the MCP tool has already been selected and executed**.

That means it cannot currently improve the initial tool proposal by rewriting the query or retrieving better schema context. Its main purpose is to demonstrate:

```text
durable state != bounded model working context
```

This is a useful architectural foundation, but retrieval/query-understanding should eventually happen earlier in the request path.
