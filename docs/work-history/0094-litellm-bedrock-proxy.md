# Work 0094 — LiteLLM Bedrock-first model proxy

## Milestone

Epic #167, issue #168 (1 of 4)

## Branch

`feat/168-litellm-proxy`

## Worktree

`.worktrees/168-litellm-proxy`

## Goal

Opt-in LiteLLM Proxy and `LiteLLMClient` behind `LLMClient`, Bedrock aliases, rollback preserved.

## Starting state

No LiteLLM code; ADR 0010 deferred it. Bedrock is the main path, so two gateways are acceptable.

## Decisions

- ADR 0012 supersedes the deferral. Stateless proxy, local Compose overlay only; AWS deploy documented, not applied.
- `LiteLLMClient` subclasses `ServeLLMClient` (same OpenAI/SSE code) via a `_prepare_request` hook and error prefix.
- Planner alias for tool-proposal calls, answer alias otherwise.

## Verification

- `uv run --no-sync pytest -q` in `services/app`: 229 passed (includes `tests/test_litellm_llm_client.py`).
- `docker compose ... config -q` with the overlay: valid.
- Not run: live proxy start, Bedrock direct-vs-proxy comparison, multi-turn and rollback smokes (manual, need AWS).

## PR / merge state

Draft PR pending.

## Lessons

`ThinkingSplitter` holds back short chunks, so streamed deltas may be coalesced.
