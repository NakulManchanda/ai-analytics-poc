# 0052 — v4.1 Voice Input Backend: WebSocket `/ws/voice` and Amazon Transcribe Streaming

## Goal

Implement the server-side audio ingestion and real-time speech-to-text pipeline for Milestone v4.1 (Voice Input) per ADR [0008-v4-voice-input-websocket-architecture.md](../decisions/0008-v4-voice-input-websocket-architecture.md), supporting low-latency 16kHz PCM audio streaming to Amazon Transcribe with a deterministic local fake provider for testing.

## Starting point

Milestone v4.1 requires continuous microphone audio streaming over a bidirectional WebSocket to transcribe user speech in real time. Prior to this change, the backend only supported unary HTTP REST endpoints (`/api/ask`) and Server-Sent Events (`/api/runs/{run_id}/events`). No WebSocket endpoints, PCM audio streaming handlers, or Amazon Transcribe streaming SDK integrations existed.

## Decisions

- **Streaming WebSocket Endpoint (`/ws/voice`)**: Added a FastAPI WebSocket endpoint at `/ws/voice` accepting binary PCM audio frames (16kHz, 16-bit mono, 100ms / 3200-byte chunks) and JSON control messages (`{"type": "stop"}`).
- **Provider Protocol Abstraction**: Created `STTProvider` and `STTSession` protocols in `app.voice.transcribe` with async callbacks (`on_partial_transcript`, `on_final_transcript`, `on_error`).
- **Amazon Transcribe Streaming Integration**: Integrated `amazon-transcribe` (`TranscribeStreamingClient`) using AWS CRT async audio streaming, converting live PCM byte streams into continuous transcription events with partial/final stability detection.
- **Deterministic Fake STT Provider**: Built `FakeSTTProvider` yielding progressive simulated partial transcripts and final text based on audio chunk volume, enabling complete local test coverage and zero-cost offline development without AWS credentials.
- **Provider Selection via Configuration**: `get_stt_provider()` auto-selects `AmazonTranscribeProvider` when `STT_PROVIDER=transcribe` or `LLM_PROVIDER=bedrock`, and defaults to `FakeSTTProvider` for local testing.
- **Compose & Makefile Support**: Updated `docker-compose.bedrock.yml` with `STT_PROVIDER: transcribe`, created `docker-compose.aws.yml` symlink, and added `local-aws-compose` target to `Makefile`.

## Verification and status

- Added 8 unit tests in `services/app/tests/test_voice.py` covering:
  - WebSocket connection handshake & immediate closure
  - Streaming audio chunks receiving partial and final transcript events
  - Stop control frames triggering session completion
  - Unrecognized text message handling
  - Exception propagation and error frame emission
  - Factory provider resolution for both fake and real Transcribe modes
- `uv run --project services/app pytest services/app/tests` passed (129/129 tests).
- `uv run --project services/app pytest tests/scripts/test_port_configuration.py` passed (4/4 tests).
- `uv run --project services/app ruff check services/app` passed cleanly.
- `uv run --project services/app black --check services/app` passed cleanly.

## Next steps

- PR #2: React frontend integration (AudioWorklet 16kHz PCM downsampler, WebSocket client, live waveform visualizer, mic button, and transcript insertion into query input).
