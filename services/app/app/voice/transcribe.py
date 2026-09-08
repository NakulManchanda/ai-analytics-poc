from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

logger = logging.getLogger(__name__)

TranscriptCallback = Callable[[str, bool], Awaitable[None]]  # (text, is_partial)


class STTSession(Protocol):
    """Active speech-to-text session for a single audio stream."""

    async def send_audio(self, chunk: bytes) -> None:
        """Forward a raw PCM audio chunk into the speech recognizer."""
        ...

    async def close(self) -> None:
        """Signal end-of-stream to recognizer and await completion."""
        ...


class STTProvider(Protocol):
    """Factory to create speech-to-text sessions."""

    async def open_session(
        self,
        on_transcript: TranscriptCallback,
    ) -> STTSession:
        """Open a new streaming transcription session."""
        ...


# Backwards compatibility alias
STTClient = STTProvider


class FakeSTTSession:
    """Deterministic fake session for offline unit tests and local FakeLLM development."""

    def __init__(
        self,
        on_transcript: TranscriptCallback,
        canned_transcript: str = "What was the average fare in Manhattan in January 2026?",
    ) -> None:
        self._on_transcript = on_transcript
        self._canned_transcript = canned_transcript
        self._total_bytes = 0
        self._chunks_received = 0
        self._closed = False

    async def send_audio(self, chunk: bytes) -> None:
        if self._closed:
            return
        self._total_bytes += len(chunk)
        self._chunks_received += 1

        # Emit progressive partial transcripts every 3 chunks
        if self._chunks_received == 3:
            words = self._canned_transcript.split()
            partial = " ".join(words[: min(4, len(words))])
            await self._on_transcript(partial, True)
        elif self._chunks_received == 6:
            words = self._canned_transcript.split()
            partial = " ".join(words[: min(7, len(words))])
            await self._on_transcript(partial, True)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # On close, emit the final transcript
        await self._on_transcript(self._canned_transcript, False)


class FakeSTTProvider:
    """Provides FakeSTTSession instances."""

    def __init__(
        self,
        canned_transcript: str = "What was the average fare in Manhattan in January 2026?",
    ) -> None:
        self.canned_transcript = canned_transcript

    async def open_session(
        self,
        on_transcript: TranscriptCallback,
    ) -> STTSession:
        return FakeSTTSession(
            on_transcript=on_transcript,
            canned_transcript=self.canned_transcript,
        )


class AmazonTranscribeSession:
    """Real Amazon Transcribe streaming session using amazon-transcribe."""

    def __init__(
        self,
        client: Any,
        on_transcript: TranscriptCallback,
        language_code: str = "en-US",
        sample_rate_hz: int = 16000,
    ) -> None:
        self._client = client
        self._on_transcript = on_transcript
        self._language_code = language_code
        self._sample_rate_hz = sample_rate_hz
        self._stream: Any = None
        self._handler_task: asyncio.Task[None] | None = None
        self._closed = False

    async def initialize(self) -> None:
        from amazon_transcribe.handlers import TranscriptResultStreamHandler
        from amazon_transcribe.model import TranscriptEvent

        self._stream = await self._client.start_stream_transcription(
            language_code=self._language_code,
            media_sample_rate_hz=self._sample_rate_hz,
            media_encoding="pcm",
        )

        session_self = self

        class StreamHandler(TranscriptResultStreamHandler):
            async def handle_transcript_event(
                self, transcript_event: TranscriptEvent
            ) -> None:
                results = transcript_event.transcript.results
                for result in results:
                    is_partial = bool(result.is_partial)
                    for alt in result.alternatives:
                        text = alt.transcript.strip()
                        if text:
                            await session_self._on_transcript(text, is_partial)

        handler = StreamHandler(self._stream.output_stream)
        self._handler_task = asyncio.create_task(handler.handle_events())

    async def send_audio(self, chunk: bytes) -> None:
        if self._closed or not self._stream:
            return
        try:
            await self._stream.input_stream.send_audio_event(audio_chunk=chunk)
        except Exception as err:
            logger.warning("Failed to send audio chunk to Transcribe: %s", err)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._stream:
            try:
                await self._stream.input_stream.end_stream()
            except Exception as err:
                logger.debug("Transcribe end_stream notice: %s", err)
        if self._handler_task and not self._handler_task.done():
            try:
                await asyncio.wait_for(self._handler_task, timeout=5.0)
            except (TimeoutError, Exception) as err:
                logger.debug("Transcribe handler task завершение: %s", err)


class AmazonTranscribeProvider:
    """Real Amazon Transcribe streaming provider."""

    def __init__(self, region: str | None = None) -> None:
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        from amazon_transcribe.client import TranscribeStreamingClient

        self._client = TranscribeStreamingClient(region=self.region)

    async def open_session(
        self,
        on_transcript: TranscriptCallback,
    ) -> STTSession:
        session = AmazonTranscribeSession(
            client=self._client,
            on_transcript=on_transcript,
        )
        await session.initialize()
        return session


def get_stt_provider() -> STTProvider:
    """Factory to instantiate the configured STT provider."""
    provider_type = os.getenv("STT_PROVIDER", "").strip().lower()

    if not provider_type:
        llm_provider = os.getenv("LLM_PROVIDER", "").strip().lower()
        if llm_provider == "bedrock":
            provider_type = "transcribe"
        else:
            provider_type = "fake"

    if provider_type == "transcribe":
        logger.info("Initializing Amazon Transcribe streaming STT provider")
        return AmazonTranscribeProvider()

    logger.info("Initializing Fake STT provider (offline/local mode)")
    return FakeSTTProvider()
