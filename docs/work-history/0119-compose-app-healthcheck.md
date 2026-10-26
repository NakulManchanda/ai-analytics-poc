# 0119 — Compose app healthcheck startup budget

## Goal
`make compose-smoke` stops failing with "container app-1 is unhealthy" on a loaded machine.

## Starting point
The `app` healthcheck in `docker-compose.yml` ran a Python one-liner with `interval: 1s`, `timeout: 1s`, `retries: 10` and no `start_period`. On a host with load average around 6 to 10 (other stacks running), unchanged main failed `make compose-smoke` twice in a row before any SSE check ran. CI passed on the same code.

## Decisions
- Only the `app` healthcheck changed: `timeout: 3s`, `retries: 30`, `start_period: 30s`, `interval` kept at 1s so a healthy app is still detected fast.
- Other services were checked and left alone: `mcp` already has `start_period: 5s` and a 60-retry budget, `redis` is a trivial `redis-cli ping`, and the litellm overlay checks already have `timeout: 3s` and a `start_period`. No overlay overrides the `app` healthcheck.
- Out of scope: app code, startup time itself.

## Verification
- Red: unchanged main, `make compose-smoke` twice under load: both failed with "container ...-app-1 is unhealthy".
- Green: with the change, `make compose-smoke` passed (22 "SSE check" lines, "Compose UI smoke passed"); no `ai-analytics-m5-smoke-*` containers left.
- `docker compose config -q` parses for the base file and the base + aws + litellm + observability combination.

## PR / merge state
Branch `fix/compose-app-healthcheck`; PR #213.

## Lessons
- A healthcheck with no `start_period` and a 1s timeout measures host load, not app health; budget the startup explicitly.
