# ADR 0009 — V5 Voice Output: AWS Polly TTS with Option B (Full-Answer Synthesis)

## Status
Accepted

## Decision
Implement v5 (voice output) using:
- **AWS Polly** for text-to-speech (not native speech models yet, not streaming per-token)
- **Option B strategy:** Wait for complete answer text, synthesize once, stream audio to browser
- **YAML configuration** for voice settings (provider, voice name, enable/disable, future streaming toggle)
- **WebAudio API** for browser playback
- **SSE extension** for audio transport (reuse existing event stream)

## Context

### v5 Scope
Agent speaks back the text answer. User hears synthesized response while reading text. Natural voice interaction without Pipecat.

### Why Option B over Option A?

| Aspect | Option A (Per-Token) | Option B (Full-Answer) | Choice |
|--------|---------------------|----------------------|--------|
| **Implementation** | Complex (coordinate token stream + Polly calls) | Simple (one Polly call) | B ✓ |
| **API calls** | Many (~50 calls per answer) | One | B ✓ |
| **Cost** | Higher (per-call overhead) | Lower | B ✓ |
| **Audio quality** | Fragmented, gaps between chunks | Unified, natural pacing | B ✓ |
| **Latency feel** | Slower (overhead on each token) | Fast (one call, then stream) | B ✓ |
| **Streaming** | Yes, but complex | No, but acceptable (<5s) | B ✓ |

**Rationale:** Most taxi agent answers are < 5 seconds. User can wait for synthesis, then hears natural audio stream. Option A adds complexity for marginal benefit. Can add per-sentence or per-chunk streaming in v5.2 if needed.

## Architecture

```
FastAPI orchestration loop
  │
  ├─ Generate answer text (existing v2 logic)
  │
  ├─ if voice.enabled:
  │   ├─ await polly.synthesize(answer_text) → audio_bytes
  │   └─ emit SSE event: {"type": "answer.audio", "data": "<base64-audio>"}
  │
  └─ emit SSE: {"type": "run.completed", ...}

Browser (React)
  │
  ├─ Receive SSE answer.audio event
  │   ├─ decode base64
  │   ├─ play audio via WebAudio
  │
  ├─ Display text answer (existing v2 logic)
  │
  └─ Show speaker icon during playback
```

## Configuration

**YAML config (settings.yml or env vars):**

```yaml
voice:
  enabled: true              # Enable/disable audio output
  provider: "polly"          # "polly" | "openai" | "native" (future)
  voice_name: "Joanna"       # Polly voice (Joanna, Matthew, Ivy, etc.)
  language: "en-US"          # Language code for Polly
  streaming: false           # v5.1: false (wait for full answer)
                             # v5.2+: true (per-sentence streaming)
```

**Python settings (Pydantic):**

```python
class VoiceSettings(BaseSettings):
    enabled: bool = True
    provider: str = "polly"
    voice_name: str = "Joanna"
    language: str = "en-US"
    streaming: bool = False
```

## Implementation Approach

### Backend (FastAPI)

1. **Polly integration:**
   - Use boto3 `Polly` client (already available in AWS SDK)
   - Method: `synthesize_speech(Text=answer_text, Voice=voice_name, OutputFormat='mp3')`
   - Returns audio bytes

2. **SSE event emission:**
   - Extend existing SSE event handler
   - Add new event type: `answer.audio`
   - Payload: base64-encoded audio bytes

3. **Orchestration flow:**
   - After answer text is complete, check `voice.enabled`
   - If enabled: call Polly, encode audio, emit SSE event
   - If disabled: skip (text-only mode)

4. **Error handling:**
   - Polly API down? Log, continue without audio (graceful degradation)
   - Invalid voice name? Use default fallback
   - Large answer? Polly has size limits (~3000 chars per call); split if needed (v5.2)

### Frontend (React)

1. **Audio event handler:**
   - Listen for `answer.audio` SSE events
   - Decode base64 → audio blob
   - Create `<audio>` element or WebAudio player

2. **UI indicators:**
   - Show speaker icon during answer
   - Play/pause button (optional)
   - Mute toggle (optional, defer to v5.2)

3. **State management:**
   - Track `isPlayingAudio` state
   - Pause audio on cancellation (v3 stop button)
   - Resume if needed

## User Experience

**Scenario:**
1. User speaks: "Which pickup zones..."
2. Text answer starts streaming (v2 existing)
3. Text completes, Polly synthesis happens (~1-2 sec)
4. Audio starts playing automatically
5. User reads + hears response simultaneously

**UX states:**
- Listening → Processing → Text streaming → Audio synthesis → Playing → Done

## Testing Strategy (v5.1)

1. **Unit:** Polly API mocking, event payload format
2. **Integration:** End-to-end voice question → text answer → audio playback
3. **Manual:** Speak query locally, hear response, verify audio quality
4. **Local only** (no real Polly cost yet; use mock or free tier)

## Configuration Flexibility

**Users can control:**
- `voice.enabled=false` → text-only mode (no audio)
- `voice.voice_name="Matthew"` → different voice
- `voice.language="es-ES"` → Spanish (future multilingual support)
- `voice.streaming=true` → v5.2 per-sentence streaming

**No code changes needed** to switch modes or voices.

## Limitations / Known Constraints (v5.1)

- **No streaming:** Waits for full answer before audio starts (~1-2 sec latency)
- **No interruption handling:** Audio keeps playing if user cancels run (v5.2 to add pause/stop)
- **Single voice:** No user voice selection in UI yet (configurable only)
- **No audio caching:** Re-synthesizes same answer if conversation reloaded (OK for v5.1)
- **Polly rate limits:** Subject to AWS Polly API quotas (configure soft limits)

## Future Extensions (v5.2+)

- **Per-sentence streaming:** Emit audio event for each sentence
- **Audio caching:** Store Polly responses in DynamoDB for repeated answers
- **Voice selection UI:** Dropdown to pick voice at runtime
- **Multilingual:** Support multiple languages via config
- **Native speech models:** Evaluate Claude native speech vs. Polly + Bedrock combo
- **Interruption:** Pause audio on cancel, resume if needed
- **Volume/playback controls:** UI for audio management

## References

- `ai_analytics_poc_realtime_multimodal_plan.md` — v5 milestone
- AWS Polly API docs
- WebAudio API (MDN)
- SSE event format (existing v2 implementation)

## Decision Rationale

**Simple before complex:** Option B is simpler, cheaper, and sufficient. Option A adds complexity for marginal UX improvement that can come later (v5.2). YAGNI principle applies.

**Configuration over code:** YAML-based settings avoid hard-coding voice preferences and allow switching strategies without code changes.

**Reuse existing infrastructure:** SSE already streams events; extend with audio. WebAudio API is native browser support.
