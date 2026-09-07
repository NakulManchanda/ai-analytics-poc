# 0046 — Shared Bedrock Application Allowance

## Goal

Add issue #88's shared monthly application allowance for real Bedrock calls,
without changing AWS runtime state or the deterministic local test path.

## Starting Point

Nova Micro calls were bounded per run, but separate users, sessions, and
processes had no shared durable application cap. Local Compose always used the
fake LLM.

## Decisions

- Store one UTC-month reservation counter in the existing application-state
  DynamoDB table and use a conditional update before every blocking or
  streaming Bedrock invocation.
- Reserve a conservative Nova Micro upper bound from the current request
  limits, rather than claiming exact settlement from a provider response.
- Accept only the already allowlisted Nova Micro model. Missing durable state,
  unsupported pricing, and storage failures reject calls safely.
- Keep fake Compose and automated tests as defaults. The opt-in local Bedrock
  Compose override reuses host AWS credentials read-only and requires portable
  shared-table configuration.

## Verification

- Focused unit tests cover durable reservation shape, exhaustion, concurrent
  reservation, UTC rollover, storage failure, and pre-invocation blocking.
- Existing application, orchestration, and Compose configuration tests pass.

## PR / Merge State

Draft PR for issue #88; no deployment, Terraform apply, or AWS runtime change
was performed.

## Lessons

An application-side allowance can safely reserve a conservative upper bound,
but cannot represent unrelated AWS usage or exact post-call provider billing.
The Nova Micro price snapshot must be reviewed when provider pricing, model,
region, or request token limits change.
