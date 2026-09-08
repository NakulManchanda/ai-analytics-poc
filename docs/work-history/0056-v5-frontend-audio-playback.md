# Work History Entry 0056: v5 Frontend Audio Playback with WebAudio

## Goal
Implement v5 voice output by adding frontend audio playback support for WebAudio MP3 decoding and playback of SSE `answer.audio` events (Issue #107). This enables users to hear the AI's synthesized voice response while reading the text answer.

## Starting Point
- ADR 0009 finalized the audio event contract: SSE `answer.audio` events carrying base64-encoded MP3 bytes
- Backend (in parallel PR) generates audio via AWS Polly TTS and emits audio events to the SSE stream
- Frontend had no audio playback capability; needed to add WebAudio integration

## Decisions & Changes

1. **useAudioPlayback Hook**:
   - Created a reusable React hook following the existing pattern of `useVoiceInput`
   - Decodes base64 MP3 data to Uint8Array, creates Blob with `audio/mpeg` type
   - Uses HTMLAudioElement for playback (simple, native browser support, YAGNI principle)
   - Exposes state: `isPlayingAudio`, `audioError`
   - Exposes control functions: `play()`, `pause()`, `stop()`, `resetError()`
   - Supports both auto-play and manual play modes
   - Gracefully handles errors (decode failures, unsupported formats, network issues)

2. **UI Integration**:
   - Speaker icon (🔊) displayed when audio is playing
   - Audio error banner shown if playback fails, with dismiss button
   - Mirrors existing voice error handling pattern for consistency

3. **SSE Event Handling**:
   - Registered `answer.audio` event type in EventSource listener array
   - Added handler to extract base64 data from SSE payload and call `handleAudioEvent()`
   - Integrated with existing answer.delta streaming so audio plays alongside text

4. **Cancellation Support**:
   - Audio playback stops when user clicks "Stop" button (calls `stopAudio()`)
   - Prevents audio from continuing while analysis is cancelled

5. **Testing Strategy**:
   - 13 comprehensive test cases in useAudioPlayback.test.ts
   - Tests cover initialization, event handling, auto-play modes, stop/pause, error handling, object URL lifecycle
   - Mock HTMLAudioElement and URL APIs since jsdom lacks audio playback support
   - All tests pass with Vitest

6. **TypeScript Configuration**:
   - Added `"types": ["vitest/globals", "node"]` to tsconfig.json to support test globals (`global`, `Buffer`)
   - Installed `@types/node` for Buffer type definitions

## Verification
- Web test suite: `npm test` → 41 tests passed (including 13 new audio playback tests)
- TypeScript: `tsc --noEmit` → No errors
- Production build: `vite build` → Success (227.68 kB JS, 16.78 kB CSS)
- No regressions in existing tests or functionality

## PR & Merge Status
- PR #109 created as draft
- Four logical commits:
  1. feat(audio): add useAudioPlayback hook for WebAudio MP3 playback
  2. ui(audio): add speaker icon and audio status banners
  3. feat(audio): integrate SSE answer.audio event handler and cancellation
  4. test(audio): comprehensive tests for useAudioPlayback hook

## Limitations (v5.1)
- No UI controls for play/pause (manual audio control) — deferred to v5.2
- No audio caching — re-synthesizes on conversation reload
- No interruption/resume — audio continues even if analysis cancelled (mitigated by stop button)
- Audio plays automatically by default (depends on browser autoplay policy)

## Reference
- ADR 0009: docs/decisions/0009-v5-voice-output-polly-tts.md
- Issue #107
- Backend PR: v5-polly-tts branch (parallel work)
