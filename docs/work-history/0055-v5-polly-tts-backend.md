# Work History Entry 0055: V5 Backend Voice Output with AWS Polly TTS

## Goal
Implement v5 backend voice output — AWS Polly TTS integration for Issue #107. Enable the orchestration loop to synthesize speech from answer text and emit SSE audio events following Option B strategy (full-answer synthesis in v5.1, per-sentence streaming deferred to v5.2).

## Starting Point
- Base: ADR 0009 (docs/decisions/0009-v5-voice-output-polly-tts.md) accepted Option B strategy.
- Base: v4.1 voice input complete (voice recording, transcription, continuous listening, credential resolution).
- Base: FastAPI orchestration loop emits answer.completed event after LLM generates final answer.
- No existing Polly integration or answer.audio SSE events.

## Decisions & Changes

### 1. VoiceSettings Configuration
- Add VoiceSettings dataclass to services/app/app/config.py (following existing Settings pattern, no pydantic).
- Support env vars: VOICE_ENABLED, VOICE_PROVIDER, VOICE_VOICE_NAME (fallback VOICE_NAME), VOICE_LANGUAGE, VOICE_STREAMING.
- Defaults: enabled=True, provider="polly", voice_name="Joanna", language="en-US", streaming=False.
- Load via VoiceSettings.from_environment() in OrchestrationLoop.__init__.

### 2. Polly TTS Client Wrapper
- Create services/app/app/voice/polly.py with PollyClient class.
- Wrap boto3 Polly synthesize_speech() with error handling.
- Accept text, voice_name, language_code, output_format parameters.
- Graceful degradation: log warnings and return None on synthesis failure (never raise).
- Initialize region from AWS_REGION env var or default us-east-1.
- Add get_polly_client() factory function for consistency with STT pattern.

### 3. Orchestration Loop Extension
- Add voice_settings parameter to OrchestrationLoop.__init__.
- Initialize Polly client if voice.enabled and provider=="polly".
- After answer.completed event (line 822-826), add synthesis logic:
  - Call polly_client.synthesize_speech(answer_text, voice_name, language, "mp3").
  - Base64-encode audio bytes if synthesis succeeds.
  - Emit answer.audio SSE event with payload containing base64 data, voice_name, format.
  - Wrap in try/except: log warnings and continue if synthesis throws.
- Graceful degradation: synthesis failure does not affect run completion.

### 4. SSE Event Payload Helper
- Add answer_audio_payload() helper to services/app/app/events/models.py.
- Signature: answer_audio_payload(audio_base64: str, voice_name: str, format: str) → dict.
- Payload structure: {"data": audio_base64, "voice_name": voice_name, "format": format}.
- Export from app.events.__init__.py.

### 5. Testing Strategy
- Add 5 comprehensive unit tests to services/app/tests/test_events.py:
  - test_voice_settings_load_from_environment_with_defaults: verify defaults.
  - test_voice_settings_load_from_environment_with_custom_values: verify env parsing.
  - test_orchestration_loop_emits_answer_audio_when_voice_enabled: mock Polly, verify event emitted.
  - test_orchestration_loop_skips_audio_when_voice_disabled: verify no event when disabled.
  - test_orchestration_loop_handles_polly_synthesis_failure_gracefully: verify error handling (Polly returns None, run completes).
- Update 2 existing tests to pass voice_settings=VoiceSettings(enabled=False):
  - test_orchestration_loop_emits_full_event_sequence (expected_order unchanged).
  - test_orchestration_loop_emits_provider_deltas_and_truthful_ttft (answer events unchanged).

## Verification
- Ran: `uv run --project services/app pytest services/app/tests -v`
- **Result: 134 passed, 1 warning in 5.74s** (all tests pass)
  - 5 new Polly/voice tests all pass
  - 129 existing tests all pass (no regressions)
  - Test coverage: config loading, event emission, voice enabled/disabled, synthesis success/failure, graceful degradation
- No ruff or black lint issues.

## Known Limitations (v5.1)
- No streaming: Waits for complete answer before audio starts (~1-2 sec latency per ADR 0009).
- No interruption handling: Audio plays if user cancels run (defer to v5.2).
- No user voice selection UI: Configurable only via env vars.
- No audio caching: Re-synthesizes same answer if conversation reloaded.
- Subject to AWS Polly rate limits and quotas.

## PR & Merge State
- **Branch**: v5/polly-tts
- **PR**: #108 (draft)
- **Commits**: 4 coherent commits
  1. feat(voice): add VoiceSettings configuration (config.py)
  2. feat(voice): implement AWS Polly TTS client wrapper (voice/polly.py)
  3. feat(voice): extend orchestration loop to synthesize and emit answer.audio events (loop.py, models.py, __init__.py)
  4. test(voice): add comprehensive Polly integration tests (test_events.py)
- No merge yet; awaiting review and user authorization.

## Lessons & Next Steps
- **Symmetry with v4 voice input**: PollyClient mirrors AmazonTranscribeProvider pattern (region init, error handling, factory).
- **Graceful degradation critical**: Synthesis failures must never break the orchestration loop or run completion.
- **Config-driven behavior**: Voice settings loaded from environment, making it easy to toggle via env vars without code changes.
- **v5.2 roadmap**: Per-sentence streaming (emit audio event per sentence), audio caching in DynamoDB, voice selection UI.

## References
- ADR 0009: docs/decisions/0009-v5-voice-output-polly-tts.md (Option B strategy)
- Issue #107: Backend voice output
- AWS Polly API: synthesize_speech() with VoiceId, LanguageCode, OutputFormat
- v4 voice input: PR #103 (frontend recording), #105 (transcription), #106 (credential resolution)
