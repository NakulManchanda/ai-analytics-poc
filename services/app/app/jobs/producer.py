from __future__ import annotations

import json
import logging
import os
from typing import Any, Protocol

from app.state.models import Job

logger = logging.getLogger(__name__)
ASYNC_JOBS_STREAM = "async-jobs"
DEFAULT_STREAM_MAXLEN = 2000


class JobProducer(Protocol):
    """Abstract interface for enqueuing async jobs."""

    def enqueue(self, job: Job) -> None: ...


class InMemoryJobProducer:
    """In-memory job producer for unit tests and local mock execution."""

    def __init__(self) -> None:
        self.jobs: list[Job] = []

    def enqueue(self, job: Job) -> None:
        self.jobs.append(job)


class RedisJobProducer:
    """Publishes async job requests to a Redis Stream."""

    def __init__(
        self,
        redis_url: str | None = None,
        stream_name: str = ASYNC_JOBS_STREAM,
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

    def enqueue(self, job: Job) -> None:
        try:
            client = self._get_client()
            payload = {
                "job_id": job.job_id,
                "job_type": job.job_type,
                "status": job.status,
                "created_at": job.created_at,
                "params": json.dumps(job.params),
            }
            client.xadd(
                self._stream,
                payload,
                maxlen=self._maxlen,
                approximate=True,
            )
            client.set(
                f"job:{job.job_id}",
                json.dumps(
                    {
                        "job_id": job.job_id,
                        "job_type": job.job_type,
                        "status": job.status,
                        "created_at": job.created_at,
                        "started_at": job.started_at,
                        "completed_at": job.completed_at,
                        "params": job.params,
                        "result": job.result,
                        "artifact_url": job.artifact_url,
                        "error": job.error,
                    }
                ),
                ex=86400,
            )
        except Exception as error:
            logger.warning(
                "Failed to enqueue job %s to Redis stream %s: %s",
                job.job_id,
                self._stream,
                error,
            )
