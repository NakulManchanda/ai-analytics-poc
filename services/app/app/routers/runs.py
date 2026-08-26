from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.orchestration import OrchestrationError, OrchestrationLoop

logger = logging.getLogger(__name__)
RunDispatcher = Callable[[Callable[[], None]], None]


class RunRequest(BaseModel):
    prompt: Annotated[str, Field(min_length=1, max_length=4_000)]
    conversation_id: str | None = None

    @field_validator("prompt")
    @classmethod
    def prompt_must_contain_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must contain non-whitespace text")
        return value


class RunAccepted(BaseModel):
    conversation_id: str
    message_id: str
    run_id: str
    events_url: str


def dispatch_in_thread(task: Callable[[], None]) -> None:
    threading.Thread(target=task, daemon=True).start()


def create_runs_router(
    orchestration_loop: OrchestrationLoop,
    run_dispatcher: RunDispatcher = dispatch_in_thread,
) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.post("/runs", response_model=RunAccepted, status_code=202)
    def create_run(request: RunRequest) -> RunAccepted:
        try:
            submission = orchestration_loop.prepare_run(request.prompt, request.conversation_id)
        except OrchestrationError as error:
            raise HTTPException(
                status_code=404 if error.code == "conversation_not_found" else 503,
                detail={"code": error.code, "retryable": error.retryable},
            ) from error

        def execute() -> None:
            try:
                orchestration_loop.execute(submission)
            except Exception:
                logger.exception("Run %s failed after submission", submission.run_id)

        run_dispatcher(execute)
        return RunAccepted(
            conversation_id=submission.conversation_id,
            message_id=submission.message_id,
            run_id=submission.run_id,
            events_url=f"/api/runs/{submission.run_id}/events",
        )

    return router
