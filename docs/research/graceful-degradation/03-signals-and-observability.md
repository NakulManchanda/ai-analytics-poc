# Signals and Observability

Graceful degradation should be driven by observable system facts, not by an arbitrary model-generated confidence number.

## Prefer concrete signals

Useful signals include:

```text
query resolution succeeded?
required entity resolved?
schema retrieval succeeded?
tool proposal validated?
MCP call succeeded?
query result passed sanitization/validation?
required data present?
execution budget remaining?
final generation succeeded?
```

For future retrieval systems, add evidence-oriented signals such as whether required sources were found and whether retrieval produced sufficient usable evidence.

## Be careful with `llm_confidence = 0.73`

A model emitting a numeric confidence score does not automatically make that value calibrated or suitable for routing decisions.

For this application, confidence should be derived primarily from evidence and execution state:

```text
confidence / trust decision
       │
       ├── evidence availability
       ├── reference resolution
       ├── contract validation
       ├── tool execution validity
       └── output verification
```

## Degradation should be visible

Possible semantic events:

```text
query_resolution.clarification_required
tool.retrying
run.degraded
generation.fallback
partial_result.returned
```

Useful attributes include:

```text
degradation.reason
degradation.tier
retry.attempt
retry.max_attempts
fallback.type
trusted_artifact_available
```

These should correlate with the same `run_id` and, once OpenTelemetry tracing is implemented, the same `trace_id`.

## Metrics

Potential cross-run metrics:

```text
degraded_runs_total{reason=...}
tool_retry_total{tool=...,reason=...}
tool_retry_success_total{tool=...}
clarification_required_total{reason=...}
structured_fallback_total{reason=...}
partial_result_total{reason=...}
```

Avoid putting raw prompts, user content, unbounded error messages, conversation IDs, run IDs, or trace IDs into metric labels/dimensions.

## Missing telemetry is a failure signal

If requests are flowing and a degradation path is known to be executing, its expected telemetry must also be present.

Examples:

```text
MCP failures > 0
AND
tool.failed events/metrics disappear

or

degraded runs > 0
AND
degradation reason telemetry is absent
```

This should be treated as an observability defect, not as evidence that degradation stopped happening.

## Debugging question

For any degraded run, an engineer should be able to answer:

```text
What failed or became uncertain?
Why was this degradation path selected?
What trusted information was still available?
What did the system intentionally refuse to do?
What did the user receive?
```
