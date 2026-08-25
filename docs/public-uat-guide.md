# Public Cloud UAT Guide

This is an **after-deployment** v1.1 acceptance checkpoint. It must be run only
after an operator has deployed the v1.1 application and frontend using the
existing AWS configuration. The v1.1 checkpoint is recorded as passed for
`60373f3` on task definition `:5`: after task replacement, a fresh browser
restored the DynamoDB-backed conversation, reconstructed SSE events, telemetry,
and TTFT. This does not make the environment CloudWatch-clean: missing
`REDIS_URL` causes Redis connection-refused publish/read errors, tracked in #57;
the v1.1 tag remains pending.

Set the actual deployed public origin before testing:

```bash
export APP_ORIGIN='https://<current-public-origin>'
```

## Health and browser entry point

Open `${APP_ORIGIN}` in a browser and verify the app is reachable. Optionally
check the API without triggering a model request:

```bash
curl -fsS "${APP_ORIGIN}/api/health" | python3 -m json.tool
```

## Two-turn backend-owned conversation

The first `/api/ask` request deliberately has no `conversation_id`. The backend
returns the authoritative ID; send that returned ID for the second request.

```bash
FIRST=$(curl -fsS -X POST "${APP_ORIGIN}/api/ask" \
  -H 'content-type: application/json' \
  -d '{"prompt":"Which pickup zones have the most trips?"}')
CONVERSATION_ID=$(printf '%s' "$FIRST" | python3 -c 'import json,sys; print(json.load(sys.stdin)["conversation_id"])')
RUN_1=$(printf '%s' "$FIRST" | python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')

SECOND=$(curl -fsS -X POST "${APP_ORIGIN}/api/ask" \
  -H 'content-type: application/json' \
  -d "{\"conversation_id\":\"${CONVERSATION_ID}\",\"prompt\":\"Show me the top five.\"}")
RUN_2=$(printf '%s' "$SECOND" | python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')

curl -fsS "${APP_ORIGIN}/api/conversations/${CONVERSATION_ID}" | python3 -m json.tool
curl -N "${APP_ORIGIN}/api/runs/${RUN_2}/events"
```

Acceptance observations:

- Both responses share one backend-owned conversation ID and use distinct run IDs.
- The reloaded conversation has four ordered messages and two runs with steps.
- The SSE response uses `text/event-stream`; a reconstructed completed run has
  ordered sequences and contains `run.received`, persisted step events,
  `context.reduced`, and a terminal event.
- The terminal telemetry is truthful. For the current blocking `/api/ask`
  contract, TTFT can be explicitly unavailable (`non_streaming_blocking`) rather
  than a fabricated timing value.
- In the browser, refresh after the two turns and verify the app reloads the
  conversation snapshot and truthful timeline/working-context values.

## Durable restart-recovery checkpoint

This step is intentionally separate from normal public UAT and requires an
operator who can replace the deployed application task without changing
infrastructure:

1. Record `${CONVERSATION_ID}` and `${RUN_2}` from the preceding flow.
2. Confirm the deployed application has `DYNAMODB_TABLE_NAME` configured and
   uses the existing DynamoDB table; do not use an in-memory fallback.
3. Replace/restart the ECS application task through the approved operational
   process, then repeat `GET /api/conversations/${CONVERSATION_ID}` and the SSE
   replay request.
4. Record the task/image revision, timestamp, URL, results, and any DynamoDB
   inspection in the release evidence.

Do not mark a future deployment checkpoint complete from local tests, an
unchanged deployment, or a browser refresh alone. The recorded `60373f3`
checkpoint passed, but its Redis/CloudWatch configuration gap remains open in
#57 and prevents the v1.1 tag.
