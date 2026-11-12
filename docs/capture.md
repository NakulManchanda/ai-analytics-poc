# Evaluation Run Capture

The application records **model calls and MCP tool calls** as `capture.v1` JSONL so evaluation runs can be graded and correlated with traces and spending.

## Evidence contract

| Record | Captured information |
| --- | --- |
| **Common** | `run_id`, `conversation_id`, `call_id`, tenant, trace, execution mode, prompt version |
| **Model** | Planner/answer role, model alias, tokens, latency/TTFT, cost, failure and fallback status |
| **Tool** | Tool name, argument keys/hash, query ID, outcome, row count |

Captures are correlated with run events and LiteLLM spend rows by run/call identifiers. **Model output is evidence, not ground truth**; reference facts come from the golden dataset and human review.

## Privacy and storage

- Capture is disabled unless `CAPTURE_DIR` is configured; files are append-only at `$CAPTURE_DIR/<tenant_id>/captures.jsonl`.
- **Metadata only by default.** `CAPTURE_CONTENT=1` opts in to question/answer/tool-argument text; raw tool-result rows and provider prompts are not stored by app capture.
- Records are deduplicated by run/call ID. Export and purge are supported; retention cleanup is manual.
- File locking supports a single POSIX host; it is not a multi-host distributed logging system.

**Related:** [Evaluation architecture](evals.md) · [Model access and spend](litellm-proxy.md) · [Capture implementation](../services/app/app/capture/).
