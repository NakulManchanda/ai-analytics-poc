# Prompt Prefix & KV Cache Contract

The owned inference path structures model prompts to make **shared-context reuse** observable and enable prefix-aware gateway placement.

## Prompt regions

| Region | Content | Reuse scope |
| --- | --- | --- |
| **Global prefix** | System rules, data/tool definitions | Across requests with identical instructions |
| **Conversation prefix** | Prior dialogue and tool observations | Within a conversation |
| **Unique suffix** | Current question or model step | One model call |

A stable `prefix_id` is derived from a SHA-256 digest of the shared prompt regions. Identical shared inputs produce the same identifier; added conversation evidence changes it.

## Ownership

```text
Application constructs messages + prefix metadata
                ↓
Gateway reads prefix_id and evaluates cache affinity vs load
                ↓
vLLM matches reusable token blocks in its KV prefix cache
```

The gateway does not rewrite prompts. **Matching prefix IDs do not guarantee a cache hit**: the worker must still hold compatible tokenized blocks. Placement and cache hit metrics determine whether reuse occurred.

## Request metadata

| Header | Purpose |
| --- | --- |
| `x-prefix-id`, `x-conversation-id` | Cache-affinity and session identity |
| `x-request-id`, `x-agent-step` | Per-call correlation |
| `x-tenant-id`, `x-request-priority` | Policy and queue classification |
| `x-estimated-prompt-tokens`, `x-deadline-ms` | Admission and deadline decisions |

**Implementation:** [Prefix builder](../services/app/app/prefix.py) · [Conversation renderer](../services/app/app/prefix_render.py) · [Gateway](../services/gateway/) · [Inference experiments](inference-experiments/inference-experiments-reference.md).
