"""AWS Polly text-to-speech integration for voice output synthesis."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


class PollyClient:
    """Wrapper around boto3 Polly client for text-to-speech synthesis."""

    def __init__(self, region: str | None = None) -> None:
        """Initialize Polly client.

        Args:
            region: AWS region; defaults to AWS_REGION env var or us-east-1
        """
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        try:
            import boto3

            self._client = boto3.client("polly", region_name=self.region)
            logger.debug(f"Initialized Polly client for region {self.region}")
        except Exception as err:
            logger.warning(f"Failed to initialize Polly client: {err}")
            self._client = None

    def synthesize_speech(
        self,
        text: str,
        voice_name: str = "Joanna",
        language_code: str = "en-US",
        output_format: str = "mp3",
    ) -> bytes | None:
        """Synthesize speech from text using AWS Polly.

        Args:
            text: The text to synthesize.
            voice_name: The Polly voice ID (e.g., "Joanna", "Matthew", "Ivy").
            language_code: The language code (e.g., "en-US", "es-ES").
            output_format: The output audio format ("mp3", "ogg_vorbis", "pcm").

        Returns:
            Audio bytes in the requested format, or None if synthesis fails.
            On failure, logs a warning and returns None (graceful degradation).
        """
        if not self._client:
            logger.warning("Polly client not initialized; skipping synthesis")
            return None

        if not text or not text.strip():
            logger.warning("Empty text provided to synthesize_speech")
            return None

        try:
            response = self._client.synthesize_speech(
                Text=text,
                VoiceId=voice_name,
                LanguageCode=language_code,
                OutputFormat=output_format,
            )
            audio_stream = response.get("AudioStream")
            if audio_stream:
                audio_bytes = audio_stream.read()
                logger.debug(
                    f"Synthesized {len(audio_bytes)} bytes for voice {voice_name}"
                )
                return audio_bytes
        except Exception as err:
            logger.warning(
                f"Polly synthesis failed for voice {voice_name}: {err}; "
                "continuing without audio"
            )
            return None

        logger.warning("Polly response missing AudioStream")
        return None


def get_polly_client(region: str | None = None) -> PollyClient:
    """Factory to instantiate the Polly TTS client."""
    return PollyClient(region=region)
