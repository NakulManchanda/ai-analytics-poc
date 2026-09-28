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
- `docs/work-history/0064-serve-path-wiring.md`

## Verification

- `source services/app/.venv/bin/activate && uv run --project services/app ruff check services/app infra/inference tests/inference` — 0 errors, clean lint.
- `source services/app/.venv/bin/activate && uv run --project services/app pytest services/app/tests/test_prefix_contract.py services/app/tests/test_serve_llm_client.py services/app/tests/test_gateway_bypass.py tests/inference infra/inference/tests` — all 64 tests passed.
- Existing core app tests verified: `test_ask.py`, `test_main.py`, `test_orchestration_loop.py` — all 31 passed.
- Anti-bypass and MCP isolation verified: `test_gateway_bypass.py` proves every agent model step traverses `/serve`, MCP calls remain direct, and Bedrock fails closed in vLLM configuration.

## PR and merge state

- Branch: `feat/121-serve-path`
- Worktree: `.worktrees/121-serve-path`
- Issue: #121
- Pull request: In progress (Draft PR opened as mini-milestone 1)

## Lessons

- Establishing the Prefix Token Contract early creates a clean boundary for future KV caching and prefix-affinity routing without modifying the core agent orchestration logic.
- Supporting both SSE event streams and JSON responses in the client streaming handler makes the adapter resilient to mock environments and intermediate proxy buffering.
- Explicit correlation headers (`x-prefix-id`, `x-agent-step`, `x-request-id`) enable tracing the journey of a single turn across multiple distributed inference steps without putting unbounded prompt text into log or metric labels.
