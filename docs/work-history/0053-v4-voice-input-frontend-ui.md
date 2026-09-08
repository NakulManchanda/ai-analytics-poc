# 0053 — v4.1 Voice Input Frontend: React AudioWorklet Capture, Waveform Visualizer, and Transcript Input

**Pull Request**: [#103](https://github.com/NakulManchanda/ai-analytics-poc/pull/103) (Merged `fe5debe`)
**Issue**: [#102](https://github.com/NakulManchanda/ai-analytics-poc/issues/102)

## Goal

Implement the frontend user interface and real-time audio capture for Milestone v4.1 (Voice Input) per ADR [0008-v4-voice-input-websocket-architecture.md](../decisions/0008-v4-voice-input-websocket-architecture.md), allowing users to speak their questions into the microphone, view live audio energy in an animated waveform visualizer, and receive the finalized transcript directly in the editable prompt box for review and analysis.

## Starting point

Following the merge of PR #101 (Issue #100), the backend `/ws/voice` WebSocket endpoint was in place to stream 16kHz PCM frames to Amazon Transcribe (and FakeSTT in local mode). The React frontend, however, only offered keyboard text entry without microphone access, Web Audio API processing, or WebSocket proxy configurations.

## Decisions

- **Audio Capture & Streaming Hook (`useVoiceInput`)**: Built a reusable React hook handling `navigator.mediaDevices.getUserMedia` microphone permissions, Web Audio API processing, and continuous 16kHz signed 16-bit linear PCM chunking (1600 samples / 3200 bytes per ~100ms frame) transmitted over a binary WebSocket connection to `/ws/voice`.
- **Live Waveform Visualizer (`WaveformVisualizer`)**: Implemented a responsive 16-bar audio visualizer connected to an `AnalyserNode`, dynamically reacting to ambient microphone volume levels during recording, with distinct active and finalizing states.
- **Microphone Control Button**: Added a dedicated `🎤 Voice` button to the prompt form with active listening pulse animations, quick manual stop (`⏹ Done Speaking`), and dismissible permission error banners.
- **Editable Transcript Insertion**: Delivered the server's final transcript directly into the prompt textarea upon completion, preserving user autonomy to edit, refine, or append text before submitting the analysis run.
- **Reverse Proxy Routing**: Configured `/ws/` WebSocket upgrade proxying in `web/nginx.conf` and `web/vite.config.ts` (`ws: true`).

## Verification and status

- Added unit tests in `web/src/WaveformVisualizer.test.tsx` (3/3 tests) covering:
  - Idle state hiding the component
  - Active listening state rendering 16 bars and the stop button
  - Stop button click triggering callback
  - Processing state rendering
- Added integration tests in `web/src/VoiceInput.test.tsx` (3/3 tests) covering:
  - Rendering the Voice button in prompt actions
  - Graceful handling of browser microphone permission denial (`NotAllowedError`)
  - Continuous streaming, stop frame transmission (`{"type": "stop"}`), and textarea population upon receiving `transcript.final`
- `npm --prefix web test` passed (29/29 tests across 4 test suites).
- `npm --prefix web run build` passed with zero errors/warnings.
- Python app and infra test suites (`services/app/tests` and `tests`) passed (154/154 tests).
- `git diff --check` passed cleanly.
