from __future__ import annotations

import json
import logging
import os
from typing import Any, Protocol
from uuid import uuid4

from app.jobs.producer import ASYNC_JOBS_STREAM

logger = logging.getLogger(__name__)
DEFAULT_JOB_GROUP = "async-jobs-workers"


class JobConsumer(Protocol):
    """Abstract interface for consuming async jobs from a queue."""

    def read_jobs(
        self, count: int = 1, block_ms: int = 1000
    ) -> list[tuple[str, dict[str, Any]]]: ...

    def ack(self, message_id: str) -> None: ...


class InMemoryJobConsumer:
    """In-memory job consumer for testing."""

    def __init__(
        self, messages: list[tuple[str, dict[str, Any]]] | None = None
    ) -> None:
        self._queue: list[tuple[str, dict[str, Any]]] = list(messages or [])
        self.acked: list[str] = []

    def push(self, message_id: str, data: dict[str, Any]) -> None:
        self._queue.append((message_id, data))

    def read_jobs(
        self, count: int = 1, block_ms: int = 1000
    ) -> list[tuple[str, dict[str, Any]]]:
        if not self._queue:
            return []
        items = self._queue[:count]
        self._queue = self._queue[count:]
        return items

    def ack(self, message_id: str) -> None:
        self.acked.append(message_id)


class RedisJobConsumer:
    """Consumes async jobs from a Redis Stream using a consumer group."""

    def __init__(
        self,
        redis_url: str | None = None,
        stream_name: str = ASYNC_JOBS_STREAM,
        group_name: str = DEFAULT_JOB_GROUP,
        consumer_name: str | None = None,
        redis_client: Any = None,
    ) -> None:
        self._url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        self._stream = stream_name
        self._group = group_name
        self._consumer_name = consumer_name or f"worker_{uuid4().hex[:8]}"
        self._client = redis_client
        self._group_created = False

    def _get_client(self) -> Any:
        if self._client is None:
            import redis

            self._client = redis.Redis.from_url(self._url, decode_responses=True)
        return self._client

    def _ensure_group(self) -> None:
        if self._group_created:
            return
        client = self._get_client()
        try:
            client.xgroup_create(self._stream, self._group, id="0", mkstream=True)
        except Exception as error:
            # Group already exists or other benign stream initialization error
            err_msg = str(error)
            if "BUSYGROUP" not in err_msg and "already exists" not in err_msg.lower():
                logger.debug("xgroup_create for stream %s: %s", self._stream, error)
        self._group_created = True

    def read_jobs(
        self, count: int = 1, block_ms: int = 1000
    ) -> list[tuple[str, dict[str, Any]]]:
        try:
            self._ensure_group()
            client = self._get_client()
            response = client.xreadgroup(
                self._group,
                self._consumer_name,
                {self._stream: ">"},
                count=count,
                block=block_ms,
            )
            jobs: list[tuple[str, dict[str, Any]]] = []
            if response:
                for _stream, messages in response:
                    for msg_id, raw_data in messages:
                        data = dict(raw_data)
                        if "params" in data and isinstance(data["params"], str):
                            try:
                                data["params"] = json.loads(data["params"])
                            except Exception:
                                pass
                        jobs.append((msg_id, data))
            return jobs
        except Exception as error:
            logger.warning(
                "Failed to read from Redis stream %s with group %s: %s",
                self._stream,
                self._group,
                error,
            )
            return []

    def ack(self, message_id: str) -> None:
        try:
            client = self._get_client()
            client.xack(self._stream, self._group, message_id)
        except Exception as error:
            logger.warning(
                "Failed to xack message %s in stream %s: %s",
                message_id,
                self._stream,
                error,
            )
