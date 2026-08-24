# AI Analytics POC — Realtime + Multimodal Evolution Plan

## 1. Goal

Evolve the existing NYC taxi analytics POC from a synchronous text AI application into a measurable, cancellable, real-time multimodal system while preserving the current architectural boundaries:

```text
Application Server / AI Orchestrator
        |
        +---- Bedrock / model provider
        |
        +---- MCP ----> governed taxi analytics ----> DuckDB
        |
        +---- DynamoDB ----> durable conversations / messages / runs / steps
        |
        +---- Redis ----> transient run events / cancellation / SSE coordination
```

The learning sequence is deliberate:

```text
truthful durable text app
    -> streaming text
    -> cancellation
    -> voice input
    -> voice output
    -> full-duplex voice
    -> multimodal image/audio/text
```

Use specialist models/services first:

```text
Speech-to-Text service
        -> existing text AI orchestrator / Bedrock LLM
        -> Text-to-Speech service
```

Do not start with a native speech-to-speech model. Add that later only as a comparison experiment.

---

# 2. Version / Tag Plan

Create a Git tag after each milestone is deployed and manually verified.

Suggested tags:

```text
v1.1-foundation-truthful-state
v2-streaming-text
v3-cancellable-runs
v4-voice-input
v5-voice-output
v6-full-duplex-voice
v7-multimodal
v8-native-speech-comparison
```

Each tag should represent a working, deployed checkpoint that can be demonstrated independently.

---

# 3. v1.1 — Foundation Fixes Before Streaming

## Objective

Make the current text application architecturally truthful before introducing new realtime features.

The deployed UI must display only state that actually exists in backend durable/transient systems. No demo-only synthesized context should masquerade as backend state.

## 3.1 Durable state must use DynamoDB in deployed AWS

The deployed FastAPI application must instantiate and use `DynamoDBStateRepository` instead of silently falling back to `InMemoryStateRepository`.

Persist at minimum:

```text
Conversation
Message(user)
Run
RunStep(s)
Message(assistant)
```

DynamoDB remains the durable source of truth.

Redis is not durable conversation storage.

### Acceptance test

1. Start a new conversation.
2. Ask two questions.
3. Verify DynamoDB contains the conversation, four messages, two runs and their run steps.
4. Restart the `ai-app` ECS task.
5. Reload the same conversation from DynamoDB.
6. The browser must reconstruct the conversation without relying on old React memory.

---

## 3.2 Conversation and run IDs must be explicit from the first question

The UI must display:

```text
Conversation: conv_...
Current Run:  run_...
```

as soon as they exist.

One conversation contains many user messages and many runs:

```text
Conversation conv_1
    |
    +-- user message #1 -> run_1
    +-- assistant message #1
    +-- user message #2 -> run_2
    +-- assistant message #2
```

Do not make the conversation ID visible only after a second question is submitted.

Prefer the backend to own ID generation. The browser should not become the authoritative generator of conversation identity.

---

## 3.3 Fix SSE reconnect / UI flashing

Typing in the prompt textarea must not restart the SSE connection.

The current React component tree should ensure that the lifecycle of:

```text
GET /api/runs/{run_id}/events
```

is tied to the run identity, not unrelated textarea renders.

Use stable callback references (`useCallback`) or otherwise structure the effect so prompt keystrokes do not:

```text
close EventSource
clear timeline
reconnect EventSource
reload events
```

### Acceptance test

After completing one run:

- type continuously into the next question;
- no visible timeline flash;
- browser network tools show no repeated SSE reconnect per character;
- the existing run timeline remains stable.

---

## 3.4 Remove frontend-synthesized / hardcoded Working Context

The Working Context Inspector must represent actual backend reducer output.

Remove the frontend fallback that constructs fake context using constants such as:

```text
hardcoded dataset columns
fake preview row such as ["Alpha", 3]
hardcoded budget numbers
synthetic artifact references
synthetic stored/included message counts
```

The browser must not independently claim:

```text
Stored Messages (DynamoDB): N
Messages in LLM Context: M
```

unless those values came from the backend.

### Source of truth

Backend flow:

```text
DynamoDB messages
      |
      v
ContextReducer
      +-- actual recent messages
      +-- actual older-turn summary
      +-- actual MCP schema
      +-- actual tool observations
      +-- actual budget tracker
      |
      v
context.reduced event
      |
      v
Redis/SSE
      |
      v
Context Inspector UI
```

The existing backend `ContextReducer` may keep deterministic policies such as:

```text
recent_turns_window = 2
max_preview_rows = 3
```

but the data it reduces must be real runtime state.

Hardcoded policy defaults are acceptable; hardcoded runtime facts are not.

### Acceptance test

Ask enough questions to exceed the recent-message window.

The UI should visibly prove:

```text
Stored messages > Messages in current LLM context
```

and show a real older-turn summary generated from persisted messages.

Refresh/restart and repeat the inspection using the same durable conversation.

---

## 3.5 Unify the orchestration path

Avoid maintaining one fixed orchestration implementation in the HTTP router and another richer implementation in `orchestration/loop.py`.

Target:

```text
HTTP endpoint
    -> OrchestrationLoop / RunExecutor
         -> LLM
         -> MCP
         -> ContextReducer
         -> StateRepository
         -> EventPublisher
```

The route should translate HTTP request/response concerns, not independently implement the AI workflow.

---

## 3.6 Add truthful latency instrumentation now

Before token streaming exists, record the current baseline.

Display on screen per run:

```text
End-to-end latency
LLM proposal latency
MCP/tool latency
LLM final-answer latency
Total input tokens
Total output tokens
Estimated cost
```

Add a visible field for:

```text
TTFT: not available (non-streaming)
```

This establishes that TTFT cannot be measured meaningfully until v2 streaming is implemented.

### v1.1 exit criteria

Tag only after:

- DynamoDB recovery is proven;
- conversation/messages/runs are actually persisted;
- UI state is reconstructable after refresh/restart;
- no fake Working Context data remains;
- SSE does not reconnect on prompt typing;
- IDs are visible immediately;
- latency/cost telemetry is real;
- TTFT explicitly shows as unavailable in blocking mode.

Create tag:

```text
v1.1-foundation-truthful-state
```

---

# 4. v2 — Streaming Text

## Objective

Turn the current blocking final-answer experience into an incremental text stream.

Use the existing text model on Bedrock. Do not introduce voice or Pipecat yet.

## 4.1 Request lifecycle

Prefer a run-first lifecycle:

```text
POST /api/runs
        |
        v
persist run = RECEIVED
        |
        v
return immediately

202 Accepted
{
  "conversation_id": "conv_...",
  "message_id": "msg_...",
  "run_id": "run_...",
  "events_url": "/api/runs/run_.../events"
}
```

Browser then connects immediately:

```text
GET /api/runs/{run_id}/events
```

The run executes while SSE is already connected.

## 4.2 Streaming events

Add events such as:

```text
run.received
context.loading
llm.started
tool.requested
tool.started
tool.completed
context.reduced
llm.started
answer.delta
answer.delta
answer.delta
answer.completed
run.completed
```

`answer.delta` must contain real provider-streamed output rather than splitting a completed string after the fact.

## 4.3 TTFT

Define:

```text
TTFT = timestamp(first model output token received by app)
       - timestamp(final-answer model request sent)
```

Also consider a separate user-visible metric:

```text
Time to first visible answer token
= browser receives/renders first answer.delta
  - user submits prompt
```

Display both if useful so provider TTFT and full application latency are not confused.

On-screen telemetry should show:

```text
TTFT                 420 ms
First visible token  680 ms
Generation           52 tok/s
Total                 2.4 s
```

### Exit criteria

- answer progressively appears;
- first delta is genuinely streamed from model provider;
- TTFT is measured from timestamps, not estimated;
- SSE timeline is live before completion;
- full answer is persisted after stream completion;
- partial output behavior on disconnect is defined.

Create tag:

```text
v2-streaming-text
```

---

# 5. v3 — Cancellable Runs

## Objective

Make generation interruptible and propagate cancellation through the run state machine.

Add:

```text
POST /api/runs/{run_id}/cancel
```

Suggested state path:

```text
RUNNING
   -> CANCEL_REQUESTED
   -> CANCELLED
```

Cancellation should:

- stop further output streaming;
- stop the active provider generation where supported;
- prevent new tool calls;
- propagate to expensive in-flight analytical work where safe/useful;
- preserve audit/run state;
- record partial output and actual usage/cost when available.

Display:

```text
cancel request -> provider stop latency
output tokens generated before cancellation
estimated tokens/cost avoided where measurable
```

This same primitive will later implement voice barge-in.

Create tag:

```text
v3-cancellable-runs
```

---

# 6. Specialist Voice Architecture

Use specialist services first:

```text
Browser microphone
      |
      v
Streaming Speech-to-Text
      |
      v
Existing text orchestrator
      |
      +---- Bedrock text LLM
      |
      +---- MCP / taxi analytics
      |
      v
Streaming text response
      |
      v
Streaming Text-to-Speech
      |
      v
Browser speaker
```

AWS-native baseline is preferred for the first implementation:

```text
STT: Amazon Transcribe Streaming (or equivalent specialist STT provider)
LLM: existing Amazon Bedrock text model
TTS: Amazon Polly streaming (or equivalent specialist TTS provider)
```

Keep interfaces provider-pluggable so later experiments can compare another STT/TTS provider without rewriting the agent.

Do not use a native speech-to-speech model yet.

---

# 7. v4 — Voice Input

## Objective

Allow the user to speak a taxi analytics question while preserving the existing text reasoning path.

Introduce Pipecat here if it materially simplifies the realtime media pipeline.

Pipecat scope:

```text
audio transport
frame streaming
STT integration
VAD / speech events
turn lifecycle
```

Pipecat must not become the durable conversation store or own taxi analytics authorization/tool policy.

Target flow:

```text
microphone
   -> realtime transport
   -> audio frames
   -> VAD
   -> streaming STT
   -> finalized user text turn
   -> existing Application Orchestrator
   -> MCP
   -> streaming text answer
```

Measure:

```text
time to first partial transcript
end-of-speech -> final transcript latency
end-of-turn decision latency
STT accuracy on taxi vocabulary
```

Create tag:

```text
v4-voice-input
```

---

# 8. v5 — Voice Output

## Objective

Speak the streamed answer back to the user.

Pipeline:

```text
LLM answer.delta
     -> text chunking / sentence buffering
     -> streaming TTS
     -> audio frames
     -> browser playback
```

Measure:

```text
LLM TTFT
TTS time-to-first-audio (TTFA)
first user-visible/heard audio latency
end-to-end turn latency
```

The UI should expose a latency waterfall such as:

```text
STT finalize      180 ms
Agent/tool        140 ms
LLM TTFT          310 ms
TTS TTFA          160 ms
------------------------
First audio       790 ms
```

Create tag:

```text
v5-voice-output
```

---

# 9. v6 — Full-Duplex Voice

## Objective

Support natural interruption / barge-in.

Add:

```text
VAD
turn detection
simultaneous receive/send media
interruption frames/events
TTS queue draining
LLM cancellation propagation
new user turn creation
```

Example:

```text
AI speaking
   |
user starts speaking
   |
   +-> detect speech
   +-> interrupt queued TTS
   +-> cancel stale LLM output
   +-> start STT for new turn
   +-> preserve durable transcript/run history
```

This is where Pipecat should provide the most value.

Measure:

```text
barge-in detection latency
TTS stop latency
LLM stop latency
stale audio emitted after interruption
```

Create tag:

```text
v6-full-duplex-voice
```

---

# 10. v7 — Multimodal Taxi Analytics

## Objective

Allow text, audio and image inputs to ground the same governed taxi analytics agent.

Examples:

```text
Upload a taxi-zone map + ask by voice:
"Which highlighted area has the most evening pickups?"

Upload a dashboard screenshot:
"Does our taxi data show the same 7 PM peak?"
```

Target architecture:

```text
Text -------------------+
                        |
Audio -> STT -----------+--> normalized multimodal context
                        |             |
Image -> vision model --+             v
                                Application Orchestrator
                                         |
                                         v
                                        MCP
                                         |
                                         v
                                  grounded taxi data
```

Use a Bedrock model that supports the required image/document modality for image understanding while preserving specialist STT/TTS for realtime voice.

Large files/images should be stored as artifacts with bounded model context and provenance.

Create tag:

```text
v7-multimodal
```

---

# 11. v8 — Native Speech-to-Speech Comparison

Only after the cascaded specialist architecture is measured, run a controlled comparison against a native realtime speech-to-speech model.

Compare:

```text
A. specialist cascade
STT -> text LLM -> TTS

B. native speech-to-speech
speech -> realtime multimodal model -> speech
```

Evaluate:

- first-audio latency;
- interruption quality;
- tool-call integration;
- observability;
- transcript/audit quality;
- provider coupling;
- cost per conversational minute;
- answer quality on grounded taxi analytics.

Create tag:

```text
v8-native-speech-comparison
```

---

# 12. Metrics Dashboard Evolution

Keep adding metrics to the same on-screen control room rather than hiding them only in logs.

## v1.1

```text
end-to-end latency
LLM proposal latency
tool latency
final LLM latency
tokens
cost
TTFT = N/A (blocking)
```

## v2

```text
TTFT
first-visible-token latency
tokens/sec
stream duration
```

## v3

```text
cancel propagation latency
partial output tokens
actual cost before cancellation
```

## v4

```text
STT first partial
STT finalization latency
turn-finalization latency
```

## v5

```text
TTS TTFA
first heard audio latency
end-to-end conversational latency
```

## v6

```text
barge-in detection latency
TTS stop latency
LLM cancellation latency
```

---

# 13. Guardrails

Preserve the original POC invariants throughout all versions:

- Application Server owns the AI execution harness.
- MCP server never calls the LLM.
- Model never determines authorization.
- DynamoDB is durable application state.
- Redis is transient coordination/event state.
- Large analytical artifacts do not belong in DynamoDB or Redis.
- Every run has bounded time, token, tool and cost budgets.
- Every external side effect is represented by durable run/step state.
- Voice/media frameworks must not become sources of business identity or authorization.

---

# 14. Immediate Codex Scope

Do **only v1.1** first.

Do not implement streaming, cancellation, STT, TTS, Pipecat or multimodal input in the first change.

The goal is to establish a trustworthy baseline before the realtime branch begins.

## Required v1.1 tasks

1. Trace how the deployed app selects `StateRepository` and wire DynamoDB for AWS runtime.
2. Persist real Conversation, Message, Run and RunStep entities.
3. Add/reuse APIs needed to reload durable conversation history.
4. Stop generating authoritative conversation state only in React memory.
5. Display `conversation_id` and current `run_id` immediately.
6. Fix SSE reconnect-on-keystroke behavior.
7. Remove frontend-generated fake Working Context.
8. Ensure Context Inspector consumes real `context.reduced` backend events/state.
9. Ensure backend ContextReducer consumes actual persisted messages, MCP schema, tool results and active budgets.
10. Add baseline latency/cost telemetry to the UI.
11. Display `TTFT: N/A (non-streaming)` for this version.
12. Add automated tests for persistence, SSE stability where practical, reducer truthfulness and conversation reload.
13. Document a manual AWS verification procedure including ECS restart + conversation recovery.
14. Do not change the user-facing analytical behavior beyond what is necessary for correctness.

## Definition of done

A reviewer can:

```text
create conversation
ask Q1
ask Q2
inspect real DynamoDB records
inspect real ContextReducer state
refresh browser
restart ECS app
reload same conversation
inspect run IDs and steps
see no UI flash while typing
see truthful latency/cost metrics
```

and all displayed state can be traced to a real backend source.

---

# 15. Codex Kickoff Prompt

Use this prompt to start the implementation:

```text
Work on repository NakulManchanda/ai-analytics-poc.

Read these first:
- ai_analytics_poc_requirements_aws_v5.md
- ai_analytics_poc_realtime_multimodal_plan.md
- docs/system-design-blog.md

Implement ONLY milestone v1.1-foundation-truthful-state from ai_analytics_poc_realtime_multimodal_plan.md.
Do not start streaming text, cancellation, voice, Pipecat, STT, TTS, or multimodal work yet.

Before changing code, inspect the current runtime path and produce a short implementation plan covering:
1. how FastAPI currently selects StateRepository in local/test/AWS runtime;
2. why the deployed DynamoDB table may be empty;
3. how conversation/message/run/run-step persistence currently works;
4. where the frontend synthesizes or hardcodes Working Context;
5. why typing can restart the SSE EventSource;
6. which APIs/state are needed to reload a conversation after refresh/restart;
7. where latency/token/cost telemetry currently comes from.

Then implement the smallest coherent change that makes the deployed application truthful:
- AWS runtime uses DynamoDBStateRepository;
- persist Conversation + user Message + Run + RunSteps + assistant Message;
- backend-generated conversation_id/run_id are returned and displayed immediately;
- reload durable conversation history from backend rather than depending on React-only chatTurns;
- fix EventSource reconnects caused by unstable React callback/effect dependencies;
- remove frontend fake Working Context fallback data (hardcoded schema, Alpha preview row, synthetic counts/budgets/artifacts);
- Context Inspector must consume actual ContextReducer/backend state;
- ContextReducer must use real persisted messages, real MCP schema/tool observations and actual budget tracker values;
- add visible baseline telemetry: end-to-end latency, proposal LLM latency, tool latency, final-answer LLM latency, tokens, cost;
- display TTFT as `N/A (non-streaming)` in this milestone.

Preserve existing architecture boundaries:
- Application Server owns orchestration and LLM calls;
- MCP server owns governed analytical tools and never calls the LLM;
- DynamoDB is durable state;
- Redis/SSE is transient event delivery;
- do not add new infrastructure.

Add/update tests before declaring completion.

Verification must include:
1. two-turn conversation;
2. DynamoDB contains conversation/messages/runs/steps;
3. browser refresh restores conversation;
4. ECS/app restart does not lose conversation;
5. typing a new question after a completed run does not reconnect or flash the SSE timeline;
6. Context Inspector values can be traced to backend runtime state;
7. existing analytics questions still work.

After implementation, summarize:
- files changed;
- architecture changes;
- tests run/results;
- manual AWS verification commands/steps;
- any remaining gaps.

Do not create the Git tag automatically. Stop once v1.1 is ready for human verification and propose the tag name `v1.1-foundation-truthful-state`.
```
