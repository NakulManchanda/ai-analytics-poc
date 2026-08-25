# Local User Acceptance Testing Guide

This guide verifies the current v1.1 local contract. It uses the deterministic
local stack and does not make a claim about an AWS deployment or ECS restart
recovery.

## Start the local stack

From the repository root:

```bash
docker compose up --build -d
```

The web gateway is available at <http://localhost:3000>. The Compose path uses
the local fake LLM, so these checks do not invoke Bedrock.

## One durable-conversation API flow

The server creates the conversation ID on the first request. Reuse that ID for
the second turn; do not invent a client-side conversation ID.

```bash
FIRST=$(curl -sS -X POST http://localhost:3000/api/ask \
  -H 'content-type: application/json' \
  -d '{"prompt":"Which pickup zones have the most trips?"}')
CONVERSATION_ID=$(printf '%s' "$FIRST" | python3 -c 'import json,sys; print(json.load(sys.stdin)["conversation_id"])')
RUN_1=$(printf '%s' "$FIRST" | python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')

SECOND=$(curl -sS -X POST http://localhost:3000/api/ask \
  -H 'content-type: application/json' \
  -d "{\"conversation_id\":\"${CONVERSATION_ID}\",\"prompt\":\"Show me the top five.\"}")
RUN_2=$(printf '%s' "$SECOND" | python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')

curl -sS "http://localhost:3000/api/conversations/${CONVERSATION_ID}" | python3 -m json.tool
curl -N "http://localhost:3000/api/runs/${RUN_2}/events"
```

Confirm all of the following:

- The two responses share `conversation_id` and have distinct `run_id` values.
- `GET /api/conversations/{conversation_id}` returns four ordered messages
  (`user`, `assistant`, `user`, `assistant`), two runs, and nonempty run steps.
- The event endpoint returns `Content-Type: text/event-stream` and its durable
  replay orders `run.received`, `step.llm_proposal`, `step.tool_call`,
  `context.reduced`, `step.llm_final_answer`, and `run.completed` with sequences
  starting at one.
- The `context.reduced` event contains a reconstructed `working_context`; the
  terminal event reports actual tokens and marks TTFT unavailable with
  `non_streaming_blocking` for this synchronous API.

For a fast, deterministic equivalent of this flow without Docker, run:

```bash
uv run --project services/app pytest services/app/tests/test_v11_integration_smoke.py -q
```

It creates a fresh FastAPI app and TestClient over the same injected
`InMemoryStateRepository`, proving API reconstruction independently of the
first app's in-memory event publisher. It intentionally does **not** prove
process-restart persistence: `InMemoryStateRepository` is only the local/test
default.

## Browser check

1. Open <http://localhost:3000> and submit a question.
2. Confirm the UI stores the backend-returned conversation ID, not a generated
   client ID; submit a second question and confirm the run ID changes.
3. Refresh while the local Compose process remains running and verify the
   conversation snapshot and run details render from the API.
4. Inspect the timeline and working context. Values must come from the durable
   snapshot/replayed events. A synchronous run may show TTFT as unavailable.

Do not use a local container or process restart as a durability acceptance
test. That is the separate deployed DynamoDB checkpoint below.

## Post-deployment AWS checkpoint (not run by this local guide)

After the v1.1 image and frontend have been deployed with the existing
`DYNAMODB_TABLE_NAME` configuration, an operator must repeat the two-turn flow,
restart/replace the ECS application task, reload the conversation, and inspect
the DynamoDB-backed result. Record the deployed URL, image/task revision, and
observed recovery separately. This repository change does not perform that
deployment or claim that recovery has occurred.

## Existing isolated Compose smoke

`make integration-smoke` retains the existing five-service isolated Compose
smoke (health, `/api/ask`, jobs, replay availability, and static proxy). It is
useful alongside the focused v1.1 API test above; it is not a substitute for
the post-deployment DynamoDB restart checkpoint.

## Teardown

When this local stack is no longer needed:

```bash
docker compose down
```
