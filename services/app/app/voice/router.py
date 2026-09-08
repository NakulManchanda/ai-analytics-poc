from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.voice.transcribe import STTProvider, get_stt_provider

logger = logging.getLogger(__name__)


def create_voice_router(default_provider: STTProvider | None = None) -> APIRouter:
    """Create the voice WebSocket router."""
    voice_router = APIRouter(tags=["voice"])

    @voice_router.websocket("/ws/voice")
    async def voice_websocket_endpoint(websocket: WebSocket) -> None:
        """Bi-directional WebSocket endpoint for streaming audio frames to speech-to-text.

        Contract:
        - Upstream binary frames: signed 16-bit PCM mono @ 16kHz (~100ms / 3200 bytes per chunk).
        - Upstream text frames: JSON controls, e.g. {"type": "stop"}.
        - Downstream text frames: JSON events:
            {"type": "voice.ready", "sample_rate": 16000, "encoding": "pcm_s16le"}
            {"type": "transcript.partial", "text": "..."}
            {"type": "transcript.final", "text": "..."}
            {"type": "voice.error", "error": "..."}
        """
        await websocket.accept()
        stt_provider = default_provider or get_stt_provider()

        session: Any = None
        try:
            # 1. Notify client that voice pipeline is ready to receive audio
            await websocket.send_json(
                {
                    "type": "voice.ready",
                    "sample_rate": 16000,
                    "encoding": "pcm_s16le",
                }
            )

            async def on_transcript(text: str, is_partial: bool) -> None:
                event_type = "transcript.partial" if is_partial else "transcript.final"
                try:
                    await websocket.send_json({"type": event_type, "text": text})
                except Exception as err:
                    logger.debug("Failed to send %s to websocket: %s", event_type, err)

            session = await stt_provider.open_session(on_transcript=on_transcript)

            # 2. Ingest stream loop
            while True:
                message = await websocket.receive()

                # Binary audio chunk
                if "bytes" in message and message["bytes"] is not None:
                    audio_bytes = message["bytes"]
                    if audio_bytes:
                        await session.send_audio(audio_bytes)

                # Text control frame
                elif "text" in message and message["text"] is not None:
                    try:
                        payload = json.loads(message["text"])
                    except Exception:
                        payload = {"type": message["text"]}

                    msg_type = payload.get("type", "").lower()
                    if msg_type in ("stop", "finish", "end"):
                        logger.debug("Client requested voice stream stop")
                        await session.close()
                        break

        except WebSocketDisconnect:
            logger.debug("Voice WebSocket disconnected by client")
        except Exception as err:
            logger.warning("Voice WebSocket processing error: %s", err, exc_info=True)
            try:
                await websocket.send_json({"type": "voice.error", "error": str(err)})
            except Exception:
                pass
        finally:
            if session:
                try:
                    await session.close()
                except Exception as err:
                    logger.debug("Error closing STT session on disconnect: %s", err)

    return voice_router


router = create_voice_router()
