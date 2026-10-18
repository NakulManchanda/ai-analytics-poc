# ADR 0012 — Introduce LiteLLM Proxy as an opt-in Bedrock-first model proxy

## Status

Accepted. Supersedes the LiteLLM deferral in [ADR 0010](0010-transferable-lambda-inference-lab.md).

## Context

Bedrock is the main model path. ADR 0010 deferred LiteLLM because it overlaps the routing and
admission mechanisms measured in the Lambda inference lab. The lab gateway is now secondary, and a
proxy also gives one place to capture requests/responses for evaluation (#169) and govern spend (#170).

## Decision

- Run LiteLLM Proxy as a separate, opt-in service selected with `LLM_PROVIDER=litellm`.
- The app keeps owning every LLM call, loop budget, and durable state; `LiteLLMClient` implements `LLMClient`.
- The agent picks stable aliases (`taxi-classifier`, `taxi-planner`, `taxi-answer`); the proxy maps them to Bedrock.
- Fake, Bedrock, and Serve clients stay as rollback options. The existing inference gateway and the
  direct `gateway_chat` replayer are unchanged and bypass the proxy.
- Two gateways coexist; routing is never chained (proxy does not front the GPU gateway).
- This slice is stateless (no database, no Admin UI).

## Consequences

- One more service to operate when the flag is on; rollback is `LLM_PROVIDER=bedrock`.
- The proxy needs the same Bedrock IAM identity and allowlist as the direct client.
