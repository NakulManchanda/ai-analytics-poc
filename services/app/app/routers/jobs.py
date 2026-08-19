from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.jobs.producer import InMemoryJobProducer, JobProducer, RedisJobProducer
from app.state import (
    InMemoryStateRepository,
    Job,
    StateRepository,
    generate_job_id,
)

logger = logging.getLogger(__name__)
ALLOWED_JOB_TYPES = {"create_full_report"}


class JobSubmitRequest(BaseModel):
    job_type: str = "create_full_report"
    params: dict[str, Any] = Field(default_factory=dict)
    conversation_id: str | None = None


class JobResponse(BaseModel):
    job_id: str
    job_type: str
    status: str
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    artifact_url: str | None = None
    error: str | None = None

    @classmethod
    def from_job(cls, job: Job) -> JobResponse:
        return cls(
            job_id=job.job_id,
            job_type=job.job_type,
            status=job.status,
            created_at=job.created_at,
            started_at=job.started_at,
            completed_at=job.completed_at,
            params=job.params,
            result=job.result,
            artifact_url=job.artifact_url,
            error=job.error,
        )


def create_jobs_router(
    state_repository: StateRepository | None = None,
    job_producer: JobProducer | None = None,
    redis_client: Any = None,
    redis_url: str | None = None,
    artifacts_dir: str | Path | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api")
    repo = state_repository or InMemoryStateRepository()

    def get_producer() -> JobProducer:
        if job_producer is not None:
            return job_producer
        url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        try:
            return RedisJobProducer(redis_url=url, redis_client=redis_client)
        except Exception:
            return InMemoryJobProducer()

    producer = get_producer()

    @router.post(
        "/jobs",
        response_model=JobResponse,
        status_code=status.HTTP_202_ACCEPTED,
        summary="Submit an asynchronous analytical job",
    )
    async def submit_job(request: JobSubmitRequest) -> JobResponse:
        if request.job_type not in ALLOWED_JOB_TYPES:
            allowed_types = list(ALLOWED_JOB_TYPES)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported job_type: '{request.job_type}'. Allowed: {allowed_types}",
            )

        job_id = generate_job_id()
        params = dict(request.params)
        if request.conversation_id:
            params["conversation_id"] = request.conversation_id

        job = Job(
            job_id=job_id,
            job_type=request.job_type,
            status="PENDING",
            params=params,
        )

        repo.create_job(job)
        producer.enqueue(job)

        return JobResponse.from_job(job)

    @router.get(
        "/jobs/{job_id}",
        response_model=JobResponse,
        summary="Get status and details of an asynchronous job",
    )
    async def get_job_status(job_id: str) -> JobResponse:
        job = repo.get_job(job_id)
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found",
            )
        return JobResponse.from_job(job)

    @router.get(
        "/jobs/{job_id}/artifact",
        summary="Get generated report artifact for a completed job",
    )
    async def get_job_artifact(job_id: str) -> dict[str, Any]:
        job = repo.get_job(job_id)
        if job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found",
            )
        if job.status != "COMPLETED" or not job.result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Artifact not available or job not completed",
            )
        return job.result

    return router
