# Retrieval Foundations

Retrieval answers:

> Which information should the application consider for this request?

It should happen before context reduction when the application has more candidate context than should be sent to the model.

## 1. Hybrid search

Hybrid search combines lexical retrieval and semantic retrieval.

```text
                    query
                      │
             ┌────────┴────────┐
             ▼                 ▼
       lexical search     semantic search
       BM25/keywords       embeddings
             │                 │
             └────────┬────────┘
                      ▼
                candidate fusion
```

### Lexical search

Strong for exact identifiers and domain terms:

- `fare_amount`
- `payment_type`
- `JFK`
- `query_taxi_data`

### Semantic search

Strong when the wording changes but the meaning remains similar.

For example:

```text
"Which neighborhoods make the most money?"
```

may need schema/context concerning:

```text
pickup_zone + total_amount + revenue aggregation
```

### Why combine them?

Semantic search can miss the importance of exact identifiers. Keyword search can miss paraphrases and conceptual similarity. Hybrid retrieval provides candidates from both.

A common later-stage design is:

```text
BM25 candidates
      +
embedding candidates
      ↓
rank fusion (for example RRF)
      ↓
combined candidate set
```

The POC does not need this machinery yet, but the context interfaces should allow it later.

---

## 2. Re-ranking

First-stage retrieval is usually optimized for recall:

> Find a small set containing everything that might be relevant.

A re-ranker then optimizes precision:

> Given these candidates and the complete request, which are actually the most useful?

```text
large corpus
    ↓
cheap retrieval
    ↓
30 candidates
    ↓
more expensive re-ranker
    ↓
5–8 candidates
```

The important serving property is that the expensive ranking model runs only over the candidate set, not the entire corpus.

Potential POC uses later:

- selecting old conversation observations;
- selecting relevant schema descriptions;
- selecting metric definitions;
- selecting previous analytical results.

---

## 3. Metadata filters

Every retrievable context object should eventually carry structured metadata, not only free text.

Example:

```json
{
  "id": "ctx_123",
  "type": "schema",
  "dataset": "nyc_taxi",
  "table": "trips",
  "columns": ["fare_amount", "pickup_zone"],
  "conversation_id": "conv_123",
  "source": "mcp",
  "version": "v3"
}
```

Then retrieval can constrain the search space before ranking:

```text
dataset = nyc_taxi
AND version = current
AND type IN (schema, metric_definition)
```

Metadata filtering matters for more than relevance. In larger systems it supports:

- tenant/user isolation;
- conversation isolation;
- schema/version correctness;
- source governance;
- time-range filtering;
- tool/dataset scoping.

A useful mental model is:

```text
structured constraints + relevance ranking
```

rather than asking embeddings to solve every filtering problem.

---

## 4. Retrieval vs context reduction

These should remain separate concepts.

```text
                 Context System

query
  │
  ▼
┌─────────────────────────┐
│ Context Retriever       │
│                         │
│ metadata filtering      │
│ lexical search          │
│ semantic search         │
│ rank fusion             │
│ re-ranking              │
└────────────┬────────────┘
             │ candidates
             ▼
┌─────────────────────────┐
│ Context Reducer         │
│                         │
│ prioritize              │
│ deduplicate             │
│ summarize/compress      │
│ truncate                │
│ enforce token budget    │
└────────────┬────────────┘
             │
             ▼
        model context
```

Retrieval decides **what might matter**. Reduction decides **what actually fits and how it is represented**.

## 5. ContextItem abstraction

A useful future boundary is to normalize candidate information into a common object before reduction.

Conceptually:

```text
ContextItem
- id
- type
- text/content

provenance
- source
- conversation_id
- run_id
- tool_call_id

semantic metadata
- dataset
- table
- columns
- metric

lifecycle
- created_at
- version

retrieval
- lexical_score
- semantic_score
- rerank_score

budgeting
- token_count
- priority
```

Then the orchestration path can evolve from deterministic selection to sophisticated retrieval without coupling the reducer to a particular vector database or search engine.
