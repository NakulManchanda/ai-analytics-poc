# Safe Degradation Ladder

The generic advice "retrieval failed, so answer from the LLM's general knowledge" is unsafe for this application. The source of truth is the dataset/tool result. A fallback must preserve that boundary.

## Tier 0 — Verified analytics

```text
Query resolution
    ↓
Tool planning
    ↓
MCP / DuckDB
    ↓
validated result
    ↓
LLM explanation
```

This is the normal path. The answer is grounded in data returned by the analytics tool.

## Tier 1 — Context degradation

Trigger: conversational intent cannot be resolved confidently.

Example:

```text
"What was the average trip distance there?"
```

If `there` cannot be resolved from conversation state, ask a clarification rather than inventing a location.

```text
uncertain referent
      ↓
clarification required
      ↓
no analytics query yet
```

The degraded capability is automation: the user must clarify. Trustworthiness is preserved.

## Tier 2 — Tool / data degradation

Trigger: schema retrieval, MCP execution, or analytics query fails.

```text
MCP / query
    ↓
 failure
    ↓
classify failure
   /       \
transient  permanent/unsafe
   │             │
retry safely     │
   │             │
   └──────┬──────┘
          ↓
if no trustworthy data
          ↓
explicit data-unavailable response
```

Do not substitute general model knowledge for missing dataset evidence.

A bounded retry may be appropriate only for failures classified as transient and safe to repeat.

## Tier 3 — Generation degradation

Trigger: the analytics query succeeded and produced validated structured data, but final natural-language generation fails.

```text
DuckDB result ✓
      ↓
final LLM ✗
      ↓
return deterministic structured result
```

This is particularly attractive for the POC because the trustworthy artifact already exists. Presentation quality is reduced without reducing evidence quality.

Example fallback:

```text
Data retrieval succeeded, but natural-language generation was unavailable.

Top pickup zone: JFK Airport
Revenue: <validated tool value>
```

## Tier 4 — Budget / time degradation

Trigger: bounded execution reaches a step, token, cost, repeated-tool-call, or deadline limit.

Prefer returning completed trusted artifacts when they exist, together with an explicit incomplete/degraded status. If no useful trusted artifact exists, terminate clearly.

The POC already treats budget exhaustion as an explicit terminal state; partial-result behavior would be an additional policy.

## Tier 5 — Platform / model degradation

Trigger: primary model/provider is unavailable or capacity is constrained.

An optional fallback model is safe only if it preserves the required contracts and trust boundaries:

```text
primary model unavailable
        ↓
compatible fallback available?
      /       \
    yes        no
     │          │
contracted    explicit
fallback      unavailable
```

A smaller model should not automatically be considered a safe fallback merely because it is available.

## Summary invariant

```text
FULL FIDELITY
     ↓
clarification
     ↓
retry transient dependency
     ↓
structured trusted result
     ↓
partial trusted result
     ↓
explicit unavailable
```

At no point should the ladder contain:

```text
analytics retrieval failed
        ↓
LLM guesses an analytics answer
```
