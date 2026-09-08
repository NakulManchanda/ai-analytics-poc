# ADR 0008 — V4 Voice Input: WebSocket + Continuous Chunking Architecture

## Status
Accepted

## Decision
Implement v4 (voice input) using:
- **WebSocket** for audio transport (not HTTP POST bulk, not WebRTC)
- **Continuous chunking** strategy (stream audio frames as captured, not record-all-then-send)
- **AWS Transcribe streaming** API for live partial + final transcript
- **Manual Start/Stop** control (no VAD or turn detection in v4.1)
- **Final transcript** delivered to editable text input (no partial display in UI)

## Context

### v4 Scope
Accept voice from browser microphone → transcribe to text → query existing agent → stream text response.
No voice output (v5), no barge-in (v6), no audio filters (v4.2+).

### Alternatives Considered

| Option | Rationale | Decision |
|--------|-----------|----------|
| **HTTP POST + bulk audio** | Simpler: record locally, send on stop | Rejected: higher latency, no live feedback, harder to extend to v5 |
| **WebRTC** | Lower latency, built for media | Rejected: overkill for v4, needs STUN/TURN, v5 will justify it |
| **EventSource instead of WebSocket** | Simpler one-way (server→client only) | Rejected: we need bidirectional (client audio→server, server transcript→client) |
| **VAD in browser** | Auto-stop on silence | Rejected: adds complexity, manual Stop is simpler for v4.1 |

## Flow

```
Browser (React UI)
  │
  ├─ User clicks "Start Listening" (mic icon)
  │   └─ Request AudioWorklet permissions & open WebSocket to /ws/voice
  │
  ├─ AudioWorklet captures microphone → emits PCM frames every ~100ms
  │   └─ Each frame sent via WebSocket to FastAPI
  │
  ├─ User sees waveform animation (live audio energy from AnalyserNode)
  │
  ├─ User clicks "Stop Listening" (stop icon)
  │   └─ AudioWorklet stops, WebSocket closes gracefully
  │
  └─ FastAPI processes frames via WebSocket handler
      │
      ├─ Stream audio to AWS Transcribe (streaming API)
      │   └─ Receive partial + final transcript events
      │
      └─ Emit final transcript via WebSocket
          │
          └─ Browser receives final transcript
              └─ Insert into text input (user can edit/correct)
              └─ User clicks "Ask" (existing /api/ask flow)
              │
              └─ SSE stream response (existing v2 flow)
```

## Architecture Components

### Browser (new)
- **AudioWorklet**: Continuous microphone capture at 16kHz PCM
- **WebSocket client**: Send PCM frames, receive transcript
- **AnalyserNode**: Live waveform visualization
- **UI state**: `isListening` (Start/Stop toggle), `transcript` (final text)

### FastAPI (new)
- **WebSocket endpoint** `/ws/voice`: 
  - Receive PCM frames from browser
  - Stream to AWS Transcribe
  - Emit final transcript back to browser
  - Graceful shutdown on disconnect

### AWS (existing service)
- **Transcribe Streaming**: Process audio frames, return partial/final transcripts

### Unchanged
- **POST /api/ask**: Takes `query` string (from transcript), runs existing orchestration
- **SSE /api/runs/{run_id}/events**: Streams answer (existing v2 flow)
- **DynamoDB RunStep**: Persist `input_transcript` field with final transcript

## Technical Details

### Audio Format
- **Codec**: PCM 16-bit signed, mono
- **Sample rate**: 16kHz (Transcribe requirement)
- **Frame size**: ~100ms chunks (1600 samples @ 16kHz)
- **Transport**: Raw bytes over WebSocket binary frame

### Transcribe Integration
- Use AWS SDK `start_stream_transcription()` with async event listener
- Emit `TranscriptEvent` (partial) and `TranscriptResultEvent` (final) back to browser
- Close stream when WebSocket disconnects

### State in FastAPI
- One active `StreamingContext` per WebSocket connection
- Track: audio buffer, Transcribe session, event stream
- Clean up on disconnect (close Transcribe stream)

### Latency Expectations
- Audio capture → server: ~50ms (network)
- Server → Transcribe: ~50ms (AWS network)
- Transcribe → final transcript: ~500ms (speech recognition)
- **Total STT latency**: ~600ms (acceptable for v4)

## User Experience

**Idle**
```
[Mic icon (blue)]
["Ask about taxi zones..." placeholder]
```

**Listening**
```
[X] [Waveform bars animating] [Stop icon]
```

**Ready to send**
```
["Best pickup zones in downtown" (editable)]
[Send button]
```

## Rationale

### Why WebSocket over HTTP bulk?
- Prepares for v5 (voice output via same connection)
- Enables live Transcribe feedback (even if not displayed in v4.1)
- Lower latency: server starts processing immediately
- No "upload time" delay before query runs

### Why continuous chunking?
- Natural streaming: matches browser's audio capture rate
- Transcribe API designed for it (lower latency on final results)
- Foundation for v6 (barge-in needs streaming)

### Why skip partial transcript display in v4.1?
- Simpler UI: no competing text with waveform
- User already used to "speak, see result, edit" (ChatGPT pattern)
- Can add partial display in v4.2 if field data shows it's valuable

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| WebSocket connection drop mid-audio | Partial transcription lost | Implement reconnect + resume logic in v4.2 |
| Transcribe API latency spikes | User waits longer for response | Log metrics, set timeout, fallback to no-voice in v4.2 |
| Browser memory from audio buffer | Long recordings consume RAM | Implement max-recording timeout (e.g., 60s) |
| Transcribe cost per request | Per-request billing | Monitor via CloudWatch, set monthly soft limit |

## Future Extensions

- **v4.2**: Add VAD (silence detection) to auto-stop
- **v4.2**: Add audio filters (high-pass, low-pass)
- **v4.2**: Display partial transcript during listening
- **v5**: Add TTS response (voice output)
- **v6**: Barge-in (interrupt agent with voice)
- **v7+**: Multi-turn voice conversations

## Testing Strategy (v4.1)

1. **Unit**: Transcribe event parsing, WebSocket message format
2. **Integration**: End-to-end voice → transcript → agent response
3. **Manual**: Speak into browser, see waveform, click stop, see transcript in input, click send, see response stream
4. **Local only** (no real AWS Transcribe cost yet; use mock if needed)

## References

- `ai_analytics_poc_realtime_multimodal_plan.md` — v4 milestone definition
- `docs/research/audio/README.md` — audio architecture details
- AWS Transcribe Streaming API docs
- WebSocket API (MDN)
