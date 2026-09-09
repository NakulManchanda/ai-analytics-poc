# Recommendations

## Recommendation in one sentence

Keep the current deterministic reducer, but evolve the architecture toward an explicit pipeline of **query understanding → retrieval → reduction → planning**, adding retrieval sophistication only when the corpus justifies it.

## Target architecture

```text
                    User message
                         │
                         ▼
                 Query Understanding
                 / Query Resolution
                         │
                  ResolvedQuery
                         │
                         ▼
                  ContextRetriever
               ┌─────────┼─────────┐
               │         │         │
          metadata    lexical   semantic
           filters     search    search
               │         │         │
               └─────────┼─────────┘
                         │
                    candidates
                         │
                      rerank
                         │
                         ▼
                   ContextReducer
               ┌─────────┼─────────┐
               │         │         │
             dedupe   compress   budget
               │         │         │
               └─────────┼─────────┘
                         │
                    WorkingContext
                         │
                         ▼
                 Tool/LLM planning
```

## Phase 1 — strengthen the existing POC

Do not add a vector database yet.

Keep:

- recent-turn window;
- deterministic older-turn summary;
- bounded tool-result previews;
- artifact references;
- execution/token/cost budgets.

Improve the internal interfaces:

1. Introduce a `ContextItem` abstraction for candidate context.
2. Separate candidate collection/retrieval from final context reduction.
3. Keep the reducer deterministic and budget-aware.
4. Add observability for why context items were included/dropped.

This provides the architecture without infrastructure expansion.

## Phase 2 — explicit query resolution

Add a `QueryRewriter` or broader `QueryUnderstanding` component before retrieval/tool planning.

Prioritize follow-up queries such as:

```text
what about airports?
and weekends?
compare that to JFK
what changed?
```

Requirements:

- preserve original query;
- produce an inspectable resolved query;
- avoid rewriting clear standalone questions unnecessarily;
- record rewrite latency/tokens/cost;
- test multi-turn reference resolution.

For this POC, this is likely a higher-value next experiment than adding a vector database.

## Phase 3 — selective deterministic retrieval

Before embeddings, use metadata and domain structure.

Examples:

- choose only schema columns relevant to the resolved analytical intent;
- retrieve observations only from the current conversation;
- prefer recent/current schema versions;
- select previous results by dataset, metric, dimensions, and run metadata.

This can demonstrate retrieval architecture while remaining cheap and explainable.

## Phase 4 — hybrid retrieval experiment

Only when the POC contains enough searchable context, compare:

```text
A: deterministic/metadata retrieval
B: lexical retrieval
C: embedding retrieval
D: lexical + embedding hybrid
```

Measure actual retrieval quality rather than assuming embeddings improve the system.

Candidate corpora:

- long conversation history;
- many prior analytical observations;
- richer semantic-model documentation;
- multiple datasets;
- metric/business definitions.

## Phase 5 — re-ranking

Add a re-ranker only when first-stage retrieval produces enough ambiguous candidates for ranking quality to matter.

Pipeline:

```text
retrieve top 20–50
       ↓
rerank
       ↓
keep top 5–8
       ↓
reducer/token budget
```

Do not run an expensive ranking model across the whole corpus.

## Where responsibilities should live

| Concern | Recommended owner |
|---|---|
| HTTP validation | `/api/ask` / router |
| durable user message | state/orchestration |
| conversational query resolution | query-understanding layer |
| metadata filters | context retriever |
| BM25/vector/hybrid search | context retriever |
| re-ranking | context retriever/ranking stage |
| deduplication | context reducer |
| summarization/compression | context reducer |
| token-budget enforcement | context reducer |
| tool selection | planner/orchestration LLM |
| MCP execution | MCP client/server boundary |

## Most important correction to the current mental model

The future path should not be:

```text
/api/ask → giant ContextReducer that does everything
```

Prefer:

```text
/api/ask
   ↓
orchestrator
   ↓
query understanding
   ↓
retriever
   ↓
reducer
   ↓
planner / tools / answer
```

This keeps each stage measurable and independently replaceable.

## Suggested experiment order

1. Build multi-turn tests that expose failures with ambiguous follow-up questions.
2. Add explicit query resolution and measure whether those tests improve.
3. Add schema/observation metadata selection.
4. Introduce `ContextItem` and inclusion/drop reasons.
5. Grow the corpus intentionally.
6. Benchmark lexical vs semantic vs hybrid retrieval.
7. Add re-ranking only if the benchmark shows a precision problem.

This sequence turns context engineering into an empirical systems project rather than adding RAG components because they are fashionable.

## Metrics worth exposing

For each run eventually capture:

```text
query_rewrite_applied
query_rewrite_latency_ms
retrieval_candidate_count
retrieval_selected_count
retrieval_latency_ms
rerank_latency_ms
context_candidate_tokens
context_included_tokens
context_reduction_ratio
context_items_dropped
context_items_dropped_by_reason
```

Also alert on expected telemetry being absent. A missing retrieval/rewrite metric is different from a legitimate value of zero and should be treated as an observability failure.
