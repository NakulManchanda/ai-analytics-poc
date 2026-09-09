# Context Engineering Research

This folder captures research and recommendations for improving context quality in the AI Analytics POC.

The main distinction used throughout these notes is:

```text
user input
   ↓
query understanding / rewrite
   ↓
retrieval
   ↓
context reduction
   ↓
LLM/tool orchestration
```

These are related but separate responsibilities.

## Documents

- [01-current-state.md](01-current-state.md) — what the code does today.
- [02-retrieval-foundations.md](02-retrieval-foundations.md) — hybrid search, re-ranking, metadata filters, and how they support context selection.
- [03-query-rewriting.md](03-query-rewriting.md) — what query rewriting is, where it should sit, and why it is not currently implemented by `/api/ask` or `ContextReducer`.
- [04-recommendations.md](04-recommendations.md) — staged recommendations for evolving the POC without prematurely turning it into a large RAG platform.

## Core mental model

The system should eventually separate two questions:

1. **Context retrieval:** Which information might matter for this request?
2. **Context reduction:** Given the candidate information, what is the smallest useful representation to place in the model context?

The current POC is much stronger on reduction than retrieval. That is appropriate for the current small dataset, but the boundary should stay explicit so retrieval can evolve later without rewriting the orchestration loop.
