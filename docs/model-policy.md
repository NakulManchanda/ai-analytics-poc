# Model Policy & Spend Governance

The application selects **planner** and **answer** models by configured roles, while enforcing tenant permissions and budget limits before invoking LiteLLM/Bedrock.

## Policy flow

```text
Agent task → Role alias → Tenant entitlement → Token/spend budget
                                         ↓
                                LiteLLM / Bedrock
                                         ↓
                                 Usage + cost record
```

| Concern | Implementation |
| --- | --- |
| **Model roles** | Configurable `classifier`, `planner`, `answer` aliases and permitted fallbacks |
| **Tenant access** | Alias allowlists; unauthorized requests are rejected |
| **Per-call limits** | Role-specific maximum output tokens |
| **Per-run limits** | Atomic in-process token and estimated-spend reservations |
| **Shared monthly cap** | Durable DynamoDB Bedrock allowance across application tasks |
| **Cost accounting** | Provider billed cost when reported; otherwise versioned estimate or explicit unknown |

Unknown usage is not silently counted as zero; budget protection conservatively reserves capacity. Model policies are **configuration-driven**, not autonomous model selection.

Current configuration maps the planner to Nova Pro and answers to Nova Micro. Per-run limits are process-local; the monthly allowance is durable.

**Code:** [Model policy](../services/app/app/model_policy/) · [Proxy integration](litellm-proxy.md) · [Evaluation](evals.md).
