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
v3.1-streaming-answer-eventsource
v4-voice-input
v5-voice-output
v6-full-duplex-voice
v7-multimodal-reasoning
v8-native-speech-comparison
v9-telephone-calls
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

## 5.1 v3.1 — Streaming Answer EventSource UI

### Objective

Complete the text streaming user experience by replacing buffered `fetch` response accumulation with real-time `EventSource` consumption on the client.

### Architecture

```text
POST /api/runs (202 Accepted)
        |
        v
GET /api/runs/{run_id}/events (EventSource)
        |
        +-- on "answer.delta" -> append chunk to streamingAnswer state (progressive character-by-character UI)
        +-- on terminal event -> extract telemetry, persist final answer, close EventSource
        +-- on cancel / abort -> close EventSource immediately, tag run as [interrupted]
```

### Exit criteria

- Answer text renders progressively character-by-character as SSE frames arrive;
- Telemetry (TTFT, latency, tokens, cost) is extracted from terminal SSE payload;
- User cancellation closes the EventSource connection and leaves no hanging streams;
- Client-side test suite verifies real-time EventSource dispatching.

Create tag:

```text
v3.1-streaming-answer-eventsource
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

# 10. v7 — Multimodal Reasoning

## Objective

Combine voice input, text queries, and audio grounding to produce richer taxi analytics reasoning.

Example:

```text
"Play me the ambient audio from last night's peak zone.
What does the pickup surge sound like in real-time?"

AI listens to audio, analyzes concurrent trip data,
and correlates what it hears with observed patterns.
```

Target architecture:

```text
Text query ----------------+
                           |
Audio -> STT ---------------+--> normalized multimodal context
                           |             |
Audio artifact playback ---+             v
                                 Application Orchestrator
                                          |
                                          v
                                         MCP
                                          |
                                          v
                                   grounded taxi data
```

Preserve specialist STT/TTS for realtime voice while integrating audio analysis into the reasoning loop.

Audio artifacts should be stored with bounded model context and clear provenance.

Create tag:

```text
v7-multimodal-reasoning
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

# 12. v9 — Telephone Calls

## Objective

Extend voice interaction to support inbound and outbound telephone calls using traditional PSTN or VoIP routing.

Scope:

```text
Inbound: customer calls a phone number -> agent answers -> MCP tools handle the query
Outbound: agent initiates a call -> customer picks up -> conversation flows
Call transfer: human handoff if needed
Call recording: durable transcript and audio artifacts
```

This milestone is deferred pending v1–v8 stability and production metrics.

Create tag:

```text
v9-telephone-calls
```

---

# 13. Metrics Dashboard Evolution

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
