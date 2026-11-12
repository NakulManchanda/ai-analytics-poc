# Local Development & Testing

The NYC Taxi analytics application can run with mocked models, AWS Bedrock through LiteLLM, or the owned GPU inference gateway. All modes expose the same FastAPI/MCP analytics surface and web UI.

## Start a runtime

| Mode | Command | When to use |
| --- | --- | --- |
| **Local mock** | `docker compose up --build -d` | Fast offline development and UI checks |
| **Managed models** | `make local-aws-litellm-compose` | Real model/tool execution with Bedrock, DynamoDB and Redis |
| **Self-hosted inference** | `make local-serve-compose` | Test gateway admission, routing and vLLM serving; requires the GPU endpoint/tunnel |

Cloud-backed modes may incur usage charges. The application uses configured request and monthly model-spend guards.

## Verify the application

1. Open [localhost:3000](http://localhost:3000) and ask: “Which pickup zones have the most trips?”
2. Check the **execution timeline** for tool selection, MCP execution, and the final answer. SSE supports progress updates and replay; token streaming depends on the selected model/agent path.
3. Submit a follow-up in the same conversation, such as “Compare that with the second-highest zone.” Conversation history behavior differs by model/agent path; see [context engineering](architecture/context-engineering-and-evidence.md).
4. Confirm service health and inspect logs:

```bash
curl -s http://localhost:3000/api/status
docker compose ps
docker compose logs -f app mcp
```

## Runtime design

- **Application:** React/FastAPI, agent orchestration, grounding, cancellation, and SSE run events.
- **Data access:** FastMCP exposes governed read-only DuckDB queries over Parquet.
- **Durable state:** DynamoDB in cloud-backed modes; Redis supports transient event coordination.
- **Models:** LiteLLM maps configured aliases to Bedrock; the owned gateway routes calls to GPU workers.
- **Voice:** Speech input/output is optional and depends on the selected runtime configuration.

## Further details

[Agent architecture](agent-harness.md) · [Model access](litellm-proxy.md) · [Inference architecture](inference.md) · [Evaluation runs](evaluation/running-evaluations.md).
