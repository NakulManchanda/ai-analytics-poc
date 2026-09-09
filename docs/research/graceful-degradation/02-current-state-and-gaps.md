# Current State and Gaps

The POC already has several resilience primitives. It does not yet have a general graceful-degradation framework, and it does not need one merely for architectural completeness.

## Existing behavior

The orchestration loop has explicit terminal/runtime outcomes including:

- completed;
- failed;
- budget exceeded;
- cancellation requested / cancelled.

The event model exposes lifecycle events such as `tool.started`, `tool.completed`, `tool.failed`, `run.failed`, and `run.budget_exceeded` so failures are visible rather than hidden.

Execution budgets bound agent behavior, including repeated equivalent tool calls.

MCP failures carry a `retryable` classification. Today that classification is propagated into the application error boundary, but the orchestration loop does not use it to perform an automatic retry.

Current tool failure behavior is approximately:

```text
MCP call
   ↓
MCPToolError
   ↓
emit tool.failed
   ↓
persist failed tool step
   ↓
raise OrchestrationError
   ↓
run.failed
```

So the POC currently has **failure classification and explicit termination**, not Tier-2 graceful retry/fallback behavior.

## Current strengths

The system already has useful building blocks for future degradation policies:

```text
bounded execution
explicit error taxonomy
retryable flag
structured tool results
run-step persistence
semantic lifecycle events
cancellation
partial streaming state
```

These are more useful than introducing a generic "degradation framework" prematurely.

## Important gaps

### Query ambiguity

Conversation-aware query resolution is being designed separately. A safe resolver should be able to return `clarification_required` instead of guessing a referent.

### Tool retry policy

`retryable=True` currently describes the error but does not cause a retry. There is no bounded retry/backoff policy around MCP execution.

### Structured-result fallback

If the MCP query succeeds but final-answer generation fails, the run currently does not intentionally convert the validated tool result into a deterministic user-facing fallback.

### Partial trusted artifacts

Budget exhaustion is explicit, but there is not yet a general policy for returning useful completed artifacts from an incomplete run.

### Provider/model fallback

There is no model-routing degradation policy, and adding one is not currently necessary for the POC.

## Recommendation

Do not implement all tiers. Treat the ladder as a design vocabulary and select small behaviors when they create a concrete reliability or learning benefit.
