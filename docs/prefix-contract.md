# Prefix Token Contract — NYC Taxi Analytics Agent

## 1. Overview and Motivation

In the NYC Taxi Analytics application, a user request initiates an agent loop that issues model calls, executes governed analytical tools (DuckDB via MCP), and answers user questions based on observations.

In GPU-served inference with vLLM, repeated generation costs can be dramatically reduced when earlier portions of the prompt are already present in the engine's KV cache (PagedAttention with prefix caching). To allow the inference gateway and routing layer to reason about KV cache reuse and prefix affinity (Issue #122), the prompt must follow an explicit **Prefix Token Contract**.

## 2. Prompt Region Breakdown

Prompts are split into three structured regions with distinct reuse scopes:

```text
+-------------------------------------------------------------+
| 1. Globally Shared Prefix (All users, all turns)            |
|    - System persona prompt                                  |
|    - NYC Taxi dataset domain constraints & rules            |
|    - Available tool & MCP schema definitions                |
+-------------------------------------------------------------+
| 2. Conversation-Shared Prefix (Within one user session)     |
|    - Prior user and assistant dialogue turns                |
|    - Model-generated tool call proposals                    |
|    - DuckDB tool observations & reduced contexts            |
+-------------------------------------------------------------+
| 3. Unique Step Suffix (Per inference step)                  |
|    - Newest question / step instructions                    |
|    - Target generation directive                            |
+-------------------------------------------------------------+
```

| Region | Reuse Scope | Content / Examples | KV Cache Implication |
|---|---|---|---|
| **Globally Shared** | Cross-tenant / Cross-session | System instructions, dataset metadata, allowed query analyses (`query_taxi_data`, `average_trip_metrics`), schemas. | Hot across all cluster workers; prime candidate for long-term KV residency. |
| **Conversation-Shared** | Within one conversation | Prior user turns, tool inputs, structured DuckDB output rows/columns. | Grows with agent steps; creates strong prefix affinity to the worker holding this session's KV blocks. |
| **Unique Step Suffix** | Single model step | Newest question text, newest observation slice, completion prompt. | Uncached; requires prefill on each invocation. |

## 3. Stable Prefix Identifier (`prefix_id`)

The prefix contract defines `prefix_id` as the 16-character SHA-256 digest of the combined shared regions:

$$\text{prefix\_id} = \text{SHA256}(\text{global\_shared} + \text{"\textbackslash n"} + \text{conversation\_shared})[0:16]$$

### Invariants:
1. Two consecutive calls with identical global rules and prior dialogue will produce the **same** `prefix_id`, even if their final question or completion suffix differs.
2. When a DuckDB tool observation is appended to the conversation context, `prefix_id` advances to a new deterministic value reflecting the extended shared prefix.

## 4. Request Metadata Transmitted to Gateway

Every inference request sent through `/serve` passes correlation metadata via HTTP headers:

| Header | Description | Example |
|---|---|---|
| `x-request-id` | Unique ID for the specific HTTP inference call | `req-c7a91b2e` |
| `x-conversation-id` | Application conversation identifier | `conv-taxi-881` |
| `x-agent-step` | Monotonic step counter within the current turn | `1` (proposal), `2` (answer) |
| `x-tenant-id` | Multi-tenant identifier for rate/admission limits | `tenant-default` |
| `x-request-priority` | Scheduling priority class | `interactive` or `batch` |
| `x-estimated-prompt-tokens` | Estimated total prompt tokens | `142` |
| `x-deadline-ms` | Request deadline timeout in milliseconds | `30000` |
| `x-prefix-id` | Stable prefix hash for router placement & KV affinity | `e4b2d19f80a3c261` |

## 5. Architectural Boundary

- **Application (`services/app`)**: Assembles prompts using `services/app/app/prefix.py`, calculates `prefix_id`, estimates token counts, and passes headers to the gateway client.
- **Gateway (`infra/inference/gateway`)**: Ingests headers without modifying prompt content; in Issue #122, uses `x-prefix-id` to route requests to the worker with highest KV cache affinity (`prefix_then_load`).
- **vLLM Workers (`infra/inference/k8s/workers`)**: Use `--enable-prefix-caching` with block-size 16 to match identical prefix blocks and skip prefill computation.
