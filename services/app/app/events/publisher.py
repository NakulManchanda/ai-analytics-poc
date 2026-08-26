from __future__ import annotations

import logging
import os
from typing import Any, Protocol

from app.events.models import RunEvent

logger = logging.getLogger(__name__)
RUN_EVENTS_STREAM = "run-events"
DEFAULT_STREAM_MAXLEN = 2000


class EventPublisher(Protocol):
    """Abstract interface for publishing transient run events."""

    def publish(self, event: RunEvent) -> None: ...


class InMemoryEventPublisher:
    """In-memory event publisher useful for unit and integration testing."""

    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    def publish(self, event: RunEvent) -> None:
        self.events.append(event)

    def get_events_for_run(self, run_id: str) -> list[RunEvent]:
        return [e for e in self.events if e.run_id == run_id]


class RedisEventPublisher:
    """Publishes transient execution events to a Redis Stream."""

    def __init__(
        self,
        redis_url: str | None = None,
        stream_name: str = RUN_EVENTS_STREAM,
        maxlen: int = DEFAULT_STREAM_MAXLEN,
        redis_client: Any = None,
    ) -> None:
        self._url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        self._stream = stream_name
        self._maxlen = maxlen
        self._client = redis_client

    def _get_client(self) -> Any:
        if self._client is None:
            import redis

            self._client = redis.Redis.from_url(self._url, decode_responses=True)
        return self._client

    def publish(self, event: RunEvent) -> None:
        try:
            client = self._get_client()
            client.xadd(
                self._stream,
                event.to_dict(),
                maxlen=self._maxlen,
                approximate=True,
            )
        except Exception as error:
            # Redis is disposable: failure to publish a transient event must not crash the run
            logger.warning("Failed to publish event %s to Redis: %s", event.event_type, error)
