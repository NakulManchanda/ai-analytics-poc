# Grounded examples (illustrative, not universal templates)

These examples reflect documentation decisions in this repository; **recheck source and evaluation data before reusing any numbers or claims**. They are not proof that every example remains true after later implementation changes.

## 1. Put the evaluated baseline up front

**Weak:** “We support ReAct, Deep Agents, and several Bedrock models.”

**Better:** “**Evaluated baseline:** Custom ReAct → LiteLLM → AWS Bedrock, with Nova Pro for tool planning and Nova Micro for grounded answer generation. In the evaluated comparative-query cases, Pro handled tool/argument selection more reliably; Micro kept answer generation inexpensive.”

**Why:** makes the actual default easy to identify, gives each model a role, and scopes the evaluation finding.

Source: [RAG & MCP architecture](../../../../docs/architecture/context-engineering-and-evidence.md).

## 2. Explain why a startup setting exists

**Weak:** “`startupProbe.failureThreshold=30` and `periodSeconds=10`.”

**Better:** “The vLLM startup probe allows up to 300 seconds for weight loading and CUDA setup without premature liveness restarts. The gateway separately waits for three healthy metric scrapes and then sends a real one-token completion before considering that worker warm for placement.”

**Why:** separates Kubernetes process health from first-inference readiness and explains the concrete reliability benefit.

Sources: [worker manifest](../../../../infra/inference/k8s/workers/worker-a.yaml) · [gateway worker registry](../../../../services/gateway/workers.py).

## 3. Distinguish three KV mechanisms

**Weak:** “Prefix routing moves and reuses the KV cache across replicas.”

**Better:** “Gateway `prefix_then_load` routing prefers the likely owner of a shared prompt prefix; vLLM `--enable-prefix-caching` reuses matching blocks **within** the selected worker. An opt-in LMCache/Mooncake connector explores **cross-worker** KV transfer; routing affinity alone is not evidence that a transfer occurred.”

**Why:** avoids conflating inferred ownership, local cache hits, and proven remote transfer.

Sources: [gateway placement](../../../../services/gateway/placement.py) · [vLLM worker](../../../../infra/inference/k8s/workers/worker-a.yaml) · [Mooncake renderer](../../../../infra/inference/mooncake/render.py).

## 4. Use correct retrieval terminology

**Weak:** “The assistant searches vector embeddings and document chunks for relevant taxi rows.”

**Better:** “The assistant retrieves structured SQL results through governed FastMCP tools backed by DuckDB/Parquet and grounds answers in returned evidence. This is SQL/tool-based retrieval, not embedding-based document RAG.”

**Why:** tells an external reader what was actually built and avoids invented architecture.

Source: [MCP server](../../../../services/mcp/mcp_server/server.py).
