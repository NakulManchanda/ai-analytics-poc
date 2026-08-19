from __future__ import annotations

import json
import logging
import os
import signal
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.events import EventPublisher, RedisEventPublisher, RunEvent
from app.jobs import JobConsumer, RedisJobConsumer
from app.mcp_client import DatasetProfileMCPClient, FastMCPDatasetProfileClient
from app.state import (
    DynamoDBStateRepository,
    InMemoryStateRepository,
    Job,
    StateRepository,
    utcnow_isoformat,
)

logger = logging.getLogger(__name__)
DEFAULT_ARTIFACTS_DIR = Path(os.getenv("ARTIFACTS_DIR", "/tmp/ai_analytics_artifacts"))


class JobWorker:
    """Background worker that executes async analytical jobs and updates durable state."""

    def __init__(
        self,
        state_repository: StateRepository,
        job_consumer: JobConsumer,
        mcp_client: DatasetProfileMCPClient | None = None,
        event_publisher: EventPublisher | None = None,
        artifacts_dir: str | Path | None = None,
    ) -> None:
        self._repo = state_repository
        self._consumer = job_consumer
        self._mcp_client = mcp_client
        self._event_publisher = event_publisher
        self._artifacts_dir = (
            Path(artifacts_dir) if artifacts_dir else DEFAULT_ARTIFACTS_DIR
        )

    def _get_mcp_client(self) -> DatasetProfileMCPClient:
        if self._mcp_client is None:
            self._mcp_client = FastMCPDatasetProfileClient()
        return self._mcp_client

    def process_one_job(self, message_id: str, data: dict[str, Any]) -> Job | None:
        job_id = data.get("job_id")
        if not job_id:
            logger.warning("Job message missing job_id: %s", data)
            self._consumer.ack(message_id)
            return None

        job = self._repo.get_job(job_id)
        if job is None:
            logger.warning("Job %s not found in state repository", job_id)
            self._consumer.ack(message_id)
            return None

        if job.status in ("COMPLETED", "FAILED"):
            logger.info("Job %s already in terminal status %s", job_id, job.status)
            self._consumer.ack(message_id)
            return job

        # Transition to RUNNING
        started_at = utcnow_isoformat()
        running_job = Job(
            job_id=job.job_id,
            job_type=job.job_type,
            status="RUNNING",
            created_at=job.created_at,
            started_at=started_at,
            params=job.params,
            metadata=job.metadata,
        )
        self._repo.update_job(running_job)

        conversation_id = job.params.get("conversation_id", job.job_id)
        if self._event_publisher:
            self._event_publisher.publish(
                RunEvent(
                    event_type="job.started",
                    run_id=job.job_id,
                    conversation_id=conversation_id,
                    sequence=1,
                    payload={
                        "job_id": job.job_id,
                        "status": "RUNNING",
                        "job_type": job.job_type,
                    },
                )
            )

        try:
            if job.job_type == "create_full_report":
                mcp = self._get_mcp_client()
                profile = mcp.get_dataset_profile()

                report_id = f"rep_{uuid4().hex[:16]}"
                completed_at = utcnow_isoformat()
                report_data = {
                    "report_id": report_id,
                    "job_id": job.job_id,
                    "title": "NYC Yellow Taxi Daily-Zone Analytics Report",
                    "generated_at": completed_at,
                    "dataset_summary": {
                        "row_count": profile.get("row_count"),
                        "zone_row_count": profile.get("zone_row_count"),
                        "timing_ms": profile.get("timing_ms"),
                        "duckdb_settings": profile.get("duckdb_settings", {}),
                    },
                    "daily_zone_rows": profile.get("daily_zone_rows", []),
                }

                # Save artifact to filesystem if configured
                artifact_url = f"/api/jobs/{job.job_id}/artifact"
                try:
                    self._artifacts_dir.mkdir(parents=True, exist_ok=True)
                    artifact_file = self._artifacts_dir / f"{job.job_id}.json"
                    artifact_file.write_text(json.dumps(report_data, indent=2))
                except Exception as file_error:
                    logger.warning(
                        "Could not write local artifact file: %s", file_error
                    )

                completed_job = Job(
                    job_id=job.job_id,
                    job_type=job.job_type,
                    status="COMPLETED",
                    created_at=job.created_at,
                    started_at=started_at,
                    completed_at=completed_at,
                    params=job.params,
                    result=report_data,
                    artifact_url=artifact_url,
                    metadata=job.metadata,
                )
                self._repo.update_job(completed_job)

                if self._event_publisher:
                    self._event_publisher.publish(
                        RunEvent(
                            event_type="job.completed",
                            run_id=job.job_id,
                            conversation_id=conversation_id,
                            sequence=2,
                            payload={
                                "job_id": job.job_id,
                                "status": "COMPLETED",
                                "artifact_url": artifact_url,
                                "summary": report_data["dataset_summary"],
                            },
                        )
                    )

                self._consumer.ack(message_id)
                logger.info("Job %s completed successfully", job.job_id)
                return completed_job
            else:
                raise ValueError(f"Unsupported job_type: {job.job_type}")

        except Exception as error:
            logger.exception("Job %s execution failed: %s", job.job_id, error)
            failed_job = Job(
                job_id=job.job_id,
                job_type=job.job_type,
                status="FAILED",
                created_at=job.created_at,
                started_at=started_at,
                completed_at=utcnow_isoformat(),
                params=job.params,
                error=str(error),
                metadata=job.metadata,
            )
            self._repo.update_job(failed_job)

            if self._event_publisher:
                self._event_publisher.publish(
                    RunEvent(
                        event_type="job.failed",
                        run_id=job.job_id,
                        conversation_id=conversation_id,
                        sequence=2,
                        payload={
                            "job_id": job.job_id,
                            "status": "FAILED",
                            "error": str(error),
                        },
                    )
                )

            self._consumer.ack(message_id)
            return failed_job

    def run(
        self,
        stop_event: threading.Event | None = None,
        once: bool = False,
        poll_interval_ms: int = 1000,
    ) -> None:
        """Run the consumer processing loop."""
        logger.info("Worker loop started")
        while True:
            if stop_event and stop_event.is_set():
                logger.info("Stop event set, exiting worker loop")
                break

            jobs = self._consumer.read_jobs(count=10, block_ms=poll_interval_ms)
            for msg_id, data in jobs:
                self.process_one_job(msg_id, data)

            if once:
                break


def create_worker(
    state_repository: StateRepository | None = None,
    job_consumer: JobConsumer | None = None,
    mcp_client: DatasetProfileMCPClient | None = None,
    event_publisher: EventPublisher | None = None,
    redis_url: str | None = None,
    table_name: str | None = None,
    artifacts_dir: str | Path | None = None,
) -> JobWorker:
    """Factory helper creating a JobWorker instance."""
    resolved_redis_url = redis_url or os.environ.get(
        "REDIS_URL", "redis://localhost:6379/0"
    )

    if state_repository is None:
        try:
            state_repository = DynamoDBStateRepository(table_name=table_name)
        except Exception:
            state_repository = InMemoryStateRepository()

    if job_consumer is None:
        job_consumer = RedisJobConsumer(redis_url=resolved_redis_url)

    if event_publisher is None:
        event_publisher = RedisEventPublisher(redis_url=resolved_redis_url)

    return JobWorker(
        state_repository=state_repository,
        job_consumer=job_consumer,
        mcp_client=mcp_client,
        event_publisher=event_publisher,
        artifacts_dir=artifacts_dir,
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Starting background worker runner...")

    stop_event = threading.Event()

    def handle_signal(sig: int, frame: Any) -> None:
        logger.info("Signal %s received, stopping worker...", sig)
        stop_event.set()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    worker = create_worker()
    worker.run(stop_event=stop_event)
    logger.info("Background worker stopped cleanly")


if __name__ == "__main__":
    main()
