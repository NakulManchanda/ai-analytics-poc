# Work History Entry 0054: Continuous Voice Input and Transcribe Credential Resolution

## Goal
Fix continuous speech listening across pauses in the voice input flow (Issue #104), and ensure reliable AWS credential resolution for Amazon Transcribe Streaming when running both locally and within Docker containers.

## Starting Point
- Base PR #101 added the backend Transcribe WebSocket route `/ws/voice`.
- Base PR #103 added the frontend voice recording components and audio worklet.
- PR #105 attempted to handle silence auto-close, but severed WebSocket connections prematurely before final transcripts could be processed, causing transcript drops.
- Additionally, `TranscribeStreamingClient` relied on `AwsCrtCredentialResolver`, which failed to resolve mounted credentials (`AWS_SHARED_CREDENTIALS_FILE`) inside Docker, causing `AWS_AUTH_CREDENTIALS_PROVIDER_IMDS_SOURCE_FAILURE`.

## Decisions & Changes
1. **Continuous Utterance and Partial/Final Transcript Handling**:
   - Amazon Transcribe streams events where in-flight speech hypotheses have `is_partial=True` (`transcript.partial`), and completed phrase/pause boundaries are finalized as `is_partial=False` (`transcript.final`).
   - **Previous design (PR #105)**: Attempted to accumulate transcripts into internal state and immediately severed `socketRef.current.close()` upon pause/stop, which cut off in-flight transcripts and stopped microphone capture prematurely.
   - **Current design (PR #106)**: The microphone stays active continuously across pauses. As each speech boundary finalizes (`is_partial=False`), `onTranscript(payload.text)` immediately appends it to the prompt textarea.
   - When the user explicitly clicks "Done Speaking", the frontend sends `{"type": "stop"}` over WebSocket and releases the microphone.
   - In `services/app/app/voice/router.py`: When the stop control frame is received and session is closed, send `{"type": "voice.completed"}` to signal completion to the client.
   - On the frontend, transition `isProcessing` to `false` upon receiving `voice.completed` (with a 3-second fallback safety timer).
2. **Explicit Boto3 StaticCredentialResolver for Amazon Transcribe**:
   - In `services/app/app/voice/transcribe.py`: Use `boto3.Session().get_credentials()` to resolve frozen AWS credentials (supporting mounted files, env vars, SSO, or profiles) and pass them to `StaticCredentialResolver` for `TranscribeStreamingClient`.
   - Wrap the event streaming task in `run_handler()` with error handling so background stream issues are logged rather than silently dropped.
3. **CI Markdown Path Filtering**:
   - In `.github/workflows/ci.yml`: Added `paths-ignore: ['**.md']` to `pull_request` and `push` triggers so documentation and markdown-only changes skip CI test runs.

## Verification
- Ran backend unit tests: `pytest services/app/tests/test_voice.py` (8 passed).
- Ran frontend tests: `vitest run` (29 passed).
- Lint and formatting validated with `ruff` and `black`.
- Live end-to-end testing with local Docker AWS stack.
