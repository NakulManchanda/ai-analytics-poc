from __future__ import annotations

import logging
import re
import threading
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.orchestration import OrchestrationError, OrchestrationLoop

logger = logging.getLogger(__name__)
RunDispatcher = Callable[[Callable[[], None]], None]


EVAL_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


class RunRequest(BaseModel):
    prompt: Annotated[str, Field(min_length=1, max_length=4_000)]
    conversation_id: str | None = None
    eval_run_id: str | None = None

    @field_validator("eval_run_id")
    @classmethod
    def eval_run_id_must_match_pattern(cls, value: str | None) -> str | None:
        if value is not None and not EVAL_RUN_ID_PATTERN.fullmatch(value):
            raise ValueError("eval_run_id must match ^[A-Za-z0-9._:-]{1,64}$")
        return value

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


class CancelRunResponse(BaseModel):
    run_id: str
    status: str


def dispatch_in_thread(task: Callable[[], None]) -> None:
    threading.Thread(target=task, daemon=True).start()


def create_runs_router(
    orchestration_loop: OrchestrationLoop,
    run_dispatcher: RunDispatcher = dispatch_in_thread,
) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.post("/runs", response_model=RunAccepted, status_code=202)
    def create_run(
        request: RunRequest,
        x_eval_run_id: Annotated[str | None, Header()] = None,
    ) -> RunAccepted:
        eval_run_id = request.eval_run_id
        if eval_run_id is None and x_eval_run_id is not None:
            if not EVAL_RUN_ID_PATTERN.fullmatch(x_eval_run_id):
                raise HTTPException(
                    status_code=422,
                    detail="x-eval-run-id must match ^[A-Za-z0-9._:-]{1,64}$",
                )
            eval_run_id = x_eval_run_id
        try:
            # Only passed when set, so default calls are identical to before.
            extra = {"eval_run_id": eval_run_id} if eval_run_id else {}
            submission = orchestration_loop.prepare_run(
                request.prompt, request.conversation_id, **extra
            )
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

    @router.post(
        "/runs/{run_id}/cancel", response_model=CancelRunResponse, status_code=202
    )
    def cancel_run(run_id: str) -> CancelRunResponse:
        try:
            run = orchestration_loop.request_cancellation(run_id)
        except OrchestrationError as error:
            if error.code == "run_not_found":
                raise HTTPException(
                    status_code=404,
                    detail={"code": error.code, "message": str(error)},
                ) from error
            if error.code == "run_already_terminal":
                raise HTTPException(
                    status_code=409,
                    detail={"code": error.code, "message": str(error)},
                ) from error
            raise HTTPException(
                status_code=500,
                detail={"code": error.code, "message": str(error)},
            ) from error
        return CancelRunResponse(run_id=run.run_id, status=run.status)

    return router
