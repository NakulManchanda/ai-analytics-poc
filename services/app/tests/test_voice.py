from __future__ import annotations

import pytest
from app.main import create_app
from app.voice.router import create_voice_router
from app.voice.transcribe import (
    FakeSTTProvider,
    get_stt_provider,
)
from fastapi.testclient import TestClient


@pytest.mark.anyio
async def test_fake_stt_session_progressively_emits_transcripts() -> None:
    events: list[tuple[str, bool]] = []

    async def callback(text: str, is_partial: bool) -> None:
        events.append((text, is_partial))

    provider = FakeSTTProvider(canned_transcript="How many rides were in Queens?")
    session = await provider.open_session(on_transcript=callback)

    dummy_chunk = b"\x01\x00" * 1600  # 100ms of 16kHz audio

    # Send 6 chunks
    for _ in range(6):
        await session.send_audio(dummy_chunk)

    # Two partials should have been emitted
    assert len(events) == 2
    assert events[0][1] is True  # is_partial
    assert events[1][1] is True  # is_partial

    # Close the session
    await session.close()

    # Final transcript should have been emitted
    assert len(events) == 3
    assert events[2][1] is False  # not partial (final)
    assert events[2][0] == "How many rides were in Queens?"


def test_voice_websocket_connect_and_stream() -> None:
    app = create_app()
    client = TestClient(app)

    with client.websocket_connect("/ws/voice") as websocket:
        # 1. Server greets with voice.ready
        ready = websocket.receive_json()
        assert ready["type"] == "voice.ready"
        assert ready["sample_rate"] == 16000
        assert ready["encoding"] == "pcm_s16le"

        # 2. Send 3 binary PCM chunks to trigger first partial
        chunk = b"\x00\x00" * 1600
        for _ in range(3):
            websocket.send_bytes(chunk)

        partial = websocket.receive_json()
        assert partial["type"] == "transcript.partial"
        assert len(partial["text"]) > 0

        # 3. Send stop control frame
        websocket.send_json({"type": "stop"})

        # 4. Server emits transcript.final
        final = websocket.receive_json()
        assert final["type"] == "transcript.final"
        assert "Manhattan" in final["text"]


def test_voice_websocket_custom_provider_injection() -> None:
    custom_provider = FakeSTTProvider(canned_transcript="Custom injected test prompt")
    custom_router = create_voice_router(default_provider=custom_provider)

    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(custom_router)
    client = TestClient(test_app)

    with client.websocket_connect("/ws/voice") as websocket:
        ready = websocket.receive_json()
        assert ready["type"] == "voice.ready"

        websocket.send_json({"type": "stop"})
        final = websocket.receive_json()
        assert final["type"] == "transcript.final"
        assert final["text"] == "Custom injected test prompt"


def test_voice_websocket_handles_disconnect_gracefully() -> None:
    app = create_app()
    client = TestClient(app)

    # Connecting and immediately dropping without sending anything should not crash
    with client.websocket_connect("/ws/voice") as websocket:
        ready = websocket.receive_json()
        assert ready["type"] == "voice.ready"


def test_voice_websocket_error_reporting() -> None:
    class FailingSTTProvider:
        async def open_session(self, on_transcript):
            raise RuntimeError("Transcribe quota exceeded")

    failing_router = create_voice_router(default_provider=FailingSTTProvider())
    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(failing_router)
    client = TestClient(test_app)

    with client.websocket_connect("/ws/voice") as websocket:
        # First message is ready
        ready = websocket.receive_json()
        assert ready["type"] == "voice.ready"

        # Second message is error reporting
        err_msg = websocket.receive_json()
        assert err_msg["type"] == "voice.error"
        assert "Transcribe quota exceeded" in err_msg["error"]


def test_get_stt_provider_defaults_to_fake_when_bedrock_not_set(monkeypatch) -> None:
    monkeypatch.delenv("STT_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    provider = get_stt_provider()
    assert isinstance(provider, FakeSTTProvider)


def test_get_stt_provider_selects_transcribe_when_explicit(monkeypatch) -> None:
    monkeypatch.setenv("STT_PROVIDER", "transcribe")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    provider = get_stt_provider()
    assert provider.__class__.__name__ == "AmazonTranscribeProvider"


def test_get_stt_provider_selects_transcribe_when_llm_is_bedrock(monkeypatch) -> None:
    monkeypatch.delenv("STT_PROVIDER", raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "bedrock")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    provider = get_stt_provider()
    assert provider.__class__.__name__ == "AmazonTranscribeProvider"
