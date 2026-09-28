# Work history 0064 — Owned serve path wiring and prefix token contract

## Goal

Connect the taxi analytics application to our owned inference cluster (`/serve`) by adding an application-owned OpenAI-compatible provider adapter (`ServeLLMClient`), establishing the Prefix Token Contract, attaching request correlation metadata, and adding a thin remote gateway proxy under `infra/inference/gateway/` for Issue #121.

## Starting point

- Issue #120 established the isolated Lambda vLLM cluster with two symmetric workers, HAMi 50/50 GPU slicing, and ClusterIP isolation (PR #131).
- The taxi agent previously called AWS Bedrock directly (or a local fake client in unit tests) without exposing token region boundaries, request correlation metadata, or cluster serving routes.
- Issue #121 authorized the application / serve-path wiring track to route all primary model calls through the owned gateway while keeping MCP tool execution direct and local.

## Decisions

- **Prefix Token Contract (`services/app/app/prefix.py` & `docs/prefix-contract.md`)**:
  - Differentiated prompt regions into globally shared (system prompt, taxi domain rules, tool/MCP schemas), conversation-shared (prior turns, model tool calls, DuckDB observations), and unique per-step suffix.
  - Implemented deterministic `prefix_id` as the 16-character SHA-256 hash of the shared text, proving invariant that differing suffixes within the same conversation preserve `prefix_id`.
- **Application Provider Adapter (`ServeLLMClient` in `services/app/app/llm.py`)**:
  - Implemented `LLMClient` protocol (`ask`, `propose_dataset_profile`, `answer_with_dataset_profile`, `propose_taxi_query`, `answer_with_query_result`, and streaming `stream_answer_with_query_result`).
  - Attached required routing/correlation headers: `x-request-id`, `x-conversation-id`, `x-agent-step`, `x-tenant-id`, `x-request-priority`, `x-estimated-prompt-tokens`, `x-deadline-ms`, `x-prefix-id`.
  - Added support for both streaming SSE chunks and JSON response fallbacks.
- **Fail-Closed Provider Selection & Bypass Guard**:
  - Maintained `LLM_PROVIDER=bedrock` as an explicit configurable fallback without automatic or silent failover.
  - Added `LLM_PROVIDER=vllm` (or `serve`) targeting `INFERENCE_GATEWAY_URL` with validation. If the cluster is unreachable, requests fail closed.
  - Implemented anti-bypass tests detecting any accidental invocation of Bedrock or direct worker bypass in course mode.
- **Strict MCP Isolation**:
  - MCP continues to communicate directly via HTTP between the app and the FastMCP service for DuckDB execution; verified that no MCP tool calls traverse the inference gateway.
- **Thin Remote Gateway (`infra/inference/gateway/`)**:
  - Implemented lightweight FastAPI gateway exposing `/health` and `/serve` (and `/v1/chat/completions`), validating incoming correlation headers and proxying to upstream vLLM workers.
  - Added Kubernetes manifests `infra/inference/k8s/gateway/gateway.yaml` and `k8s/services/gateway.yaml` (ClusterIP port 8080).
  - Updated `infra/inference/scripts/tunnel.sh` to forward remote gateway port 8080 to local port 18080.

## Files

- `services/app/app/prefix.py`
- `docs/prefix-contract.md`
- `services/app/app/config.py`
- `services/app/app/llm.py`
- `services/app/tests/test_prefix_contract.py`
- `services/app/tests/test_serve_llm_client.py`
- `services/app/tests/test_gateway_bypass.py`
- `infra/inference/gateway/__init__.py`
- `infra/inference/gateway/main.py`
- `infra/inference/k8s/gateway/gateway.yaml`
- `infra/inference/k8s/services/gateway.yaml`
- `infra/inference/scripts/tunnel.sh`
- `infra/inference/scripts/deploy.sh`
- `infra/inference/tests/test_gateway_contract.py`
- `infra/inference/tests/test_manifests_contract.py`
- `tests/inference/test_cluster_bundle_contract.py`
- `tests/inference/test_serve_path_contract.py`
- `infra/inference/scripts/gateway-restart.sh`
- `scripts/smoke/17_inference_serve.py`
- `Makefile` (targets: `inference-gateway-restart`, `inference-serve-smoke`, `app-serve-dev`)
- `docs/work-history/0064-serve-path-wiring.md`

## Verification

- `source services/app/.venv/bin/activate && uv run --project services/app black --check services/app tests scripts` — clean formatting across all files.
- `source services/app/.venv/bin/activate && uv run --project services/app ruff check services/app infra/inference tests/inference scripts` — 0 errors, clean lint.
- Full test suite: `uv run --project services/app pytest services/app/tests tests/inference` — all 194 passed.
- Anti-bypass and MCP isolation verified: `test_gateway_bypass.py` proves every agent model step traverses `/serve`, MCP calls remain direct, and Bedrock fails closed in vLLM configuration.
- Repeatable Make Targets:
  - `make inference-serve-smoke` — executed end-to-end against remote cluster:
    - Gateway Health: 200 OK
    - Direct Ask: 63 prompt tokens, 495 completion tokens, 183.8 tokens/s
    - Streaming SSE: TTFT 181.6ms, 143 tokens, 133.5 tokens/s
  - `make inference-gateway-restart` — executed rolling restart of `deployment/inference-gateway` on Lambda, verified immediate zero-reload recovery.
- Empirical Benchmark Results (Captured against Lambda A100 GPU cluster):
  - Direct Worker A (`:18001`): p50 = 477.79ms, p95 = 590.77ms
  - Remote Gateway (`:18080`): p50 = 511.46ms, p95 = 627.77ms
  - Gateway Routing Overhead: +33.67ms p50 delta (+47.23ms mean delta)
  - Streaming TTFT via Gateway: p50 = 279.49ms (min = 240.32ms, max = 320.98ms)
  - Multi-step Turn: Step 1 (Proposal) = 1,413.31ms (303 tokens), Step 2 (DuckDB query) = 14ms, Step 3 (Streamed answer) = 1,280.79ms (TTFT = 166.31ms, 213 tokens)

## PR and merge state

- Branch: `feat/121-serve-path`
- Worktree: `.worktrees/121-serve-path`
- Issue: #121
- Pull request: [PR #132](https://github.com/NakulManchanda/ai-analytics-poc/pull/132) — `feat(inference): route taxi-agent model calls through owned serve path (#121)`
- GitHub Actions exact-head CI passed cleanly.
- Ready for merge.

## Lessons

- Establishing the Prefix Token Contract early creates a clean boundary for future KV caching and prefix-affinity routing without modifying the core agent orchestration logic.
- Supporting both SSE event streams and JSON responses in the client streaming handler makes the adapter resilient to mock environments and intermediate proxy buffering.
- Explicit correlation headers (`x-prefix-id`, `x-agent-step`, `x-request-id`) enable tracing the journey of a single turn across multiple distributed inference steps without putting unbounded prompt text into log or metric labels.
- A thin stateless gateway introduces negligible overhead (~33ms p50) while ensuring consistent header instrumentation and fail-closed security.

