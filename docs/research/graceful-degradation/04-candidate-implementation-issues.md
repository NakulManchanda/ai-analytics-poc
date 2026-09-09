# Candidate Implementation Issues

These are deliberately independent implementation slices. The research notes do **not** imply that all of them should be built.

## Candidate A — Bounded retry for transient MCP failures

**Possible issue:** `Retry transient MCP/tool failures with bounded backoff`

Use the existing `MCPToolError.retryable` classification to make a small, explicit retry policy.

```text
MCPToolError
   ├── retryable=false -> fail immediately
   └── retryable=true
           ↓
       retry once
           ↓
      success / fail
```

Suggested constraints:

- maximum one retry initially;
- retry only errors explicitly classified as transient/retryable;
- respect the overall execution deadline/budget;
- emit `tool.retrying` with attempt/reason;
- persist attempts so Timeline Inspector explains the extra latency;
- test retry success and retry exhaustion;
- detect missing retry telemetry when retry behavior occurs.

**Learning value:** retry taxonomy, idempotency, backoff, deadlines, distributed tracing of retries.

**Scope:** small.

## Candidate B — Return validated tool result when final LLM generation fails

**Possible issue:** `Return deterministic structured fallback when final answer generation fails`

If MCP/DuckDB succeeded and produced a validated result but the final LLM provider fails, return a deterministic representation of the trusted result rather than discarding it.

```text
validated tool result ✓
       ↓
final generation ✗
       ↓
structured fallback
```

Requirements:

- only use already validated/sanitized tool output;
- clearly identify that natural-language generation was unavailable;
- do not invoke another ungrounded answer path;
- emit `generation.fallback` / `run.degraded`;
- distinguish `completed` from `degraded` in a deliberate way;
- test that fallback contains only data from the trusted tool result.

**Learning value:** graceful degradation with a strong trust invariant; particularly demonstrable in this POC.

**Scope:** small-to-medium.

## Candidate C — Clarification as a first-class QueryResolver outcome

**Possible issue:** extend the conversation-aware QueryResolver work so ambiguity produces a typed `clarification_required` result.

```text
"How much lower was it?"
       ↓
referent cannot be resolved safely
       ↓
clarification_required
```

Do not guess an entity merely to keep the agent moving.

Requirements:

- typed resolver outcome;
- no MCP call when required referent is unresolved;
- explicit semantic event;
- regression tests for ambiguous pronouns/ellipsis;
- UI renders clarification naturally.

**Learning value:** uncertainty handling and correctness-oriented agent design.

**Scope:** best folded into the existing query-resolution issue rather than creating a competing subsystem.

## Candidate D — Return completed trusted artifacts on budget exhaustion

**Possible issue:** `Preserve and return trusted partial artifacts when agent budget is exhausted`

Today budget exhaustion is an explicit terminal state. This candidate asks whether completed tool results should survive as a user-visible partial result.

```text
step 1 ✓
step 2 ✓ trusted artifact
step 3 -> budget exceeded
              ↓
return artifact + incomplete status
```

This requires careful semantics: not every intermediate result is meaningful or safe to present.

**Learning value:** bounded agents, partial execution semantics, checkpointing.

**Scope:** medium and easy to over-design.

## Candidate E — Model/provider fallback

**Possible issue:** `Route final generation to compatible fallback model on provider outage`

Only consider after the application has an actual availability requirement or multiple providers/models worth comparing.

A fallback model must preserve tool/output contracts. It should not silently change grounding behavior.

**Learning value:** routing, provider abstraction, availability vs quality tradeoffs.

**Scope:** medium; low priority for this POC.

## Suggested order

If choosing for engineering value rather than feature count:

```text
1. Candidate C — clarification outcome (already aligned with QueryResolver work)
2. Candidate B — deterministic structured fallback
3. Candidate A — one bounded MCP retry
4. Candidate D — partial artifacts on budget exhaustion
5. Candidate E — model/provider fallback
```

Candidate B is probably the cleanest standalone demonstration of the core principle:

> degradation reduces presentation fidelity while preserving the trusted data source.
