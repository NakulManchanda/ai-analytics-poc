# LiteLLM Model Access

LiteLLM provides a **stable model gateway for managed Bedrock models**. The application owns agent execution, budgets, state and tool calls; the proxy translates model aliases into provider requests and records usage.

## Request flow

```text
FastAPI agent → Model policy → LiteLLM proxy → AWS Bedrock
                  │                 │
             run limits       spend logs / traces
```

| Alias | Responsibility | Model |
| --- | --- | --- |
| `taxi-planner` | Select governed analytical tools | Nova Pro |
| `taxi-answer` | Generate grounded answers | Nova Micro |
| `taxi-classifier` | Reserved classification role | Nova Micro |

Routing is **configuration-driven**, not an LLM router. Model mapping lives in [proxy configuration](../infra/litellm/config.yaml).

## Security, cost and traces

- App-level model policies enforce role budgets, tenant entitlements and permitted fallbacks; a shared DynamoDB allowance limits Bedrock spending.
- AWS SDK credentials or IAM task roles authorize Bedrock requests. The proxy requires `LITELLM_API_KEY`.
- App `call_id`/`run_id` correlate capture records with proxy spend rows, including retries and fallback attempts.
- W3C trace propagation links app, MCP and proxy spans in **Phoenix**; proxy logs and optional request capture support debugging.
- Prompt/response body capture is **opt-in** (`LITELLM_CAPTURE=1`). Default logs are metadata-focused.

## Runtime boundaries

The proxy is a required dependency only on the LiteLLM path; direct Bedrock is an alternate path. **The owned vLLM inference gateway is separate from LiteLLM.** The local tracing/Compose integration is implemented; deployed ECS proxy tracing and rollout have not been verified in the documented state.

**References:** [Model policies](model-policy.md) · [Evaluation capture](capture.md) · [Local runtime guide](local-uat-guide.md) · [Architecture](inference.md).
