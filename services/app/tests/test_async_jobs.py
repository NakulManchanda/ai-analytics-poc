from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from app.events import InMemoryEventPublisher
from app.jobs import (
    InMemoryJobConsumer,
    InMemoryJobProducer,
    RedisJobConsumer,
    RedisJobProducer,
)
from app.main import create_app
from app.state import (
    DuplicateEntityError,
    EntityNotFoundError,
    InMemoryStateRepository,
    Job,
    generate_job_id,
)
from app.worker import JobWorker
from fastapi.testclient import TestClient


class FakeMCPClient:
    def __init__(self, should_fail: bool = False) -> None:
        self.should_fail = should_fail

    def get_dataset_profile(self) -> dict[str, Any]:
        if self.should_fail:
            raise RuntimeError("MCP profile service unavailable")
        return {
            "row_count": 2964624,
            "zone_row_count": 265,
            "schema_columns": [
                "PULocationID",
                "DOLocationID",
                "trip_distance",
                "total_amount",
            ],
            "daily_zone_rows": [
                {
                    "pickup_date": "2024-01-01",
                    "pickup_zone": "JFK Airport",
                    "trip_count": 1250,
                    "total_amount": 75000.5,
                }
            ],
            "duckdb_settings": {"threads": "1", "memory_limit": "512MB"},
            "timing_ms": 45,
            "rss_bytes": 104857600,
        }

    def get_dataset_schema(self) -> dict[str, Any]:
        return {
            "dataset": "nyc-yellow-taxi",
            "month": "2024-01",
            "columns": [
                "PULocationID",
                "DOLocationID",
                "trip_distance",
                "total_amount",
            ],
        }

    def query_taxi_data(self, analysis: str, limit: int = 5) -> dict[str, Any]:
        return {
            "columns": ["pickup_zone", "trip_count"],
            "rows": [["JFK Airport", 1250]],
            "row_count": 1,
            "execution_duration_ms": 15,
            "query_id": "fake-query-1",
            "truncated": False,
        }


class FakeRedisStreamClient:
    def __init__(self) -> None:
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.groups: set[tuple[str, str]] = set()
        self.acked: list[tuple[str, str, str]] = []

    def xadd(
        self,
        stream: str,
        fields: dict[str, Any],
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> str:
        stream_list = self.streams.setdefault(stream, [])
        msg_id = f"{len(stream_list) + 1}-0"
        str_fields = {k: str(v) for k, v in fields.items()}
        stream_list.append((msg_id, str_fields))
        return msg_id

    def xgroup_create(self, stream: str, group: str, id: str = "0", mkstream: bool = False) -> None:
        self.groups.add((stream, group))
        if stream not in self.streams:
            self.streams[stream] = []

    def xreadgroup(
        self,
        group: str,
        consumer: str,
        streams: dict[str, str],
        count: int = 1,
        block: int | None = None,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        results = []
        for stream_name, _id in streams.items():
            entries = self.streams.get(stream_name, [])
            if entries:
                results.append((stream_name, entries[:count]))
                # Simulate moving forward
                self.streams[stream_name] = entries[count:]
        return results

    def xack(self, stream: str, group: str, *ids: str) -> int:
        for msg_id in ids:
            self.acked.append((stream, group, msg_id))
        return len(ids)


def test_job_model_and_inmemory_repository() -> None:
    repo = InMemoryStateRepository()
    job_id = generate_job_id()
    assert job_id.startswith("job_")

    job = Job(
        job_id=job_id,
        job_type="create_full_report",
        status="PENDING",
        params={"analysis": "full"},
    )
    repo.create_job(job)

    # Duplicate creation error
    with pytest.raises(DuplicateEntityError):
        repo.create_job(job)

    retrieved = repo.get_job(job_id)
    assert retrieved is not None
    assert retrieved.job_id == job_id
    assert retrieved.status == "PENDING"
    assert retrieved.params == {"analysis": "full"}

    # Update job
    updated = Job(
        job_id=job_id,
        job_type="create_full_report",
        status="RUNNING",
        created_at=job.created_at,
        started_at="2026-08-19T00:00:00Z",
    )
    repo.update_job(updated)
    assert repo.get_job(job_id).status == "RUNNING"

    # Nonexistent update
    with pytest.raises(EntityNotFoundError):
        repo.update_job(Job(job_id="non-existent"))

    # List jobs
    jobs = repo.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].job_id == job_id


def test_submit_job_endpoint_success() -> None:
    repo = InMemoryStateRepository()
    producer = InMemoryJobProducer()
    client = TestClient(create_app(state_repository=repo, job_producer=producer))

    response = client.post(
        "/api/jobs",
        json={"job_type": "create_full_report", "params": {"sample": True}},
    )
    assert response.status_code == 202
    data = response.json()
    assert data["job_id"].startswith("job_")
    assert data["job_type"] == "create_full_report"
    assert data["status"] == "PENDING"
    assert data["params"] == {"sample": True}

    # Verify state repository has the job
    stored_job = repo.get_job(data["job_id"])
    assert stored_job is not None
    assert stored_job.status == "PENDING"

    # Verify producer enqueued the job
    assert len(producer.jobs) == 1
    assert producer.jobs[0].job_id == data["job_id"]


def test_submit_job_endpoint_validation_error() -> None:
    repo = InMemoryStateRepository()
    producer = InMemoryJobProducer()
    client = TestClient(create_app(state_repository=repo, job_producer=producer))

    response = client.post(
        "/api/jobs",
        json={"job_type": "unsupported_job_type"},
    )
    assert response.status_code == 400
    assert "Unsupported job_type" in response.json()["detail"]


def test_get_job_status_endpoint() -> None:
    repo = InMemoryStateRepository()
    client = TestClient(create_app(state_repository=repo))

    # 404 for unknown job
    res_404 = client.get("/api/jobs/job_unknown123")
    assert res_404.status_code == 404

    # 200 for existing job
    job = Job(
        job_id="job_test456",
        job_type="create_full_report",
        status="RUNNING",
        started_at="2026-08-19T00:00:00Z",
    )
    repo.create_job(job)

    res_200 = client.get("/api/jobs/job_test456")
    assert res_200.status_code == 200
    data = res_200.json()
    assert data["job_id"] == "job_test456"
    assert data["status"] == "RUNNING"
    assert data["started_at"] == "2026-08-19T00:00:00Z"


def test_get_job_artifact_endpoint(tmp_path: Path) -> None:
    repo = InMemoryStateRepository()
    client = TestClient(create_app(state_repository=repo))

    job_id = "job_art123"
    job = Job(
        job_id=job_id,
        job_type="create_full_report",
        status="COMPLETED",
        result={"report_id": "rep_1", "title": "NYC Report", "rows": [1, 2, 3]},
        artifact_url=f"/api/jobs/{job_id}/artifact",
    )
    repo.create_job(job)

    res = client.get(f"/api/jobs/{job_id}/artifact")
    assert res.status_code == 200
    data = res.json()
    assert data["report_id"] == "rep_1"
    assert data["title"] == "NYC Report"


def test_redis_job_producer_and_consumer() -> None:
    fake_redis = FakeRedisStreamClient()
    producer = RedisJobProducer(redis_client=fake_redis, stream_name="test-jobs")
    consumer = RedisJobConsumer(redis_client=fake_redis, stream_name="test-jobs")

    job = Job(
        job_id="job_redis_1",
        job_type="create_full_report",
        params={"zone": 1},
    )
    producer.enqueue(job)

    assert len(fake_redis.streams["test-jobs"]) == 1

    messages = consumer.read_jobs(count=5)
    assert len(messages) == 1
    msg_id, data = messages[0]
    assert data["job_id"] == "job_redis_1"
    assert data["params"] == {"zone": 1}

    consumer.ack(msg_id)
    assert len(fake_redis.acked) == 1
    assert fake_redis.acked[0][2] == msg_id


def test_worker_processes_job_to_completion(tmp_path: Path) -> None:
    repo = InMemoryStateRepository()
    consumer = InMemoryJobConsumer()
    events = InMemoryEventPublisher()
    mcp_client = FakeMCPClient()

    job_id = "job_worker_test_1"
    job = Job(
        job_id=job_id,
        job_type="create_full_report",
        status="PENDING",
        params={"conversation_id": "conv_123"},
    )
    repo.create_job(job)
    consumer.push("msg_1", {"job_id": job_id})

    worker = JobWorker(
        state_repository=repo,
        job_consumer=consumer,
        mcp_client=mcp_client,
        event_publisher=events,
        artifacts_dir=tmp_path,
    )

    completed_job = worker.process_one_job("msg_1", {"job_id": job_id})
    assert completed_job is not None
    assert completed_job.status == "COMPLETED"
    assert completed_job.completed_at is not None
    assert completed_job.result is not None
    assert completed_job.result["dataset_summary"]["row_count"] == 2964624

    # Verify durable state is updated
    stored = repo.get_job(job_id)
    assert stored.status == "COMPLETED"
    assert stored.result == completed_job.result

    # Verify artifact written to disk
    artifact_file = tmp_path / f"{job_id}.json"
    assert artifact_file.exists()
    file_content = json.loads(artifact_file.read_text())
    assert file_content["report_id"] == completed_job.result["report_id"]

    # Verify consumer acknowledged
    assert consumer.acked == ["msg_1"]

    # Verify events emitted
    assert len(events.events) == 2
    assert events.events[0].event_type == "job.started"
    assert events.events[0].payload["status"] == "RUNNING"
    assert events.events[1].event_type == "job.completed"
    assert events.events[1].payload["status"] == "COMPLETED"


def test_worker_handles_job_failure() -> None:
    repo = InMemoryStateRepository()
    consumer = InMemoryJobConsumer()
    events = InMemoryEventPublisher()
    failing_mcp = FakeMCPClient(should_fail=True)

    job_id = "job_fail_test"
    job = Job(job_id=job_id, job_type="create_full_report", status="PENDING")
    repo.create_job(job)
    consumer.push("msg_fail", {"job_id": job_id})

    worker = JobWorker(
        state_repository=repo,
        job_consumer=consumer,
        mcp_client=failing_mcp,
        event_publisher=events,
    )

    failed_job = worker.process_one_job("msg_fail", {"job_id": job_id})
    assert failed_job is not None
    assert failed_job.status == "FAILED"
    assert "MCP profile service unavailable" in (failed_job.error or "")

    # State updated
    stored = repo.get_job(job_id)
    assert stored.status == "FAILED"
    assert stored.error == failed_job.error

    # Consumer acknowledged
    assert consumer.acked == ["msg_fail"]

    # Event emitted
    assert any(e.event_type == "job.failed" for e in events.events)


def test_worker_run_once_loop(tmp_path: Path) -> None:
    repo = InMemoryStateRepository()
    consumer = InMemoryJobConsumer()
    mcp_client = FakeMCPClient()

    job_id = "job_loop_1"
    repo.create_job(Job(job_id=job_id, job_type="create_full_report", status="PENDING"))
    consumer.push("msg_10", {"job_id": job_id})

    worker = JobWorker(
        state_repository=repo,
        job_consumer=consumer,
        mcp_client=mcp_client,
        artifacts_dir=tmp_path,
    )

    worker.run(once=True)
    assert repo.get_job(job_id).status == "COMPLETED"
