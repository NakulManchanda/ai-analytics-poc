from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.orchestration import LoopResult, OrchestrationError, OrchestrationLoop


class AskRequest(BaseModel):
    prompt: Annotated[str, Field(min_length=1, max_length=4_000)]
    conversation_id: str | None = None

    @field_validator("prompt")
    @classmethod
    def prompt_must_contain_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must contain non-whitespace text")
        return value


class UsageMetadata(BaseModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int


class LLMCallMetadata(BaseModel):
    llm_call_id: str
    model_id: str
    usage: UsageMetadata
    latency_ms: int


class AskResponse(BaseModel):
    answer: str
    tool_call_id: str | None
    query_id: str | None
    llm_calls: list[LLMCallMetadata]
    usage: UsageMetadata
    latency_ms: int
    conversation_id: str
    run_id: str


def _response_from_result(result: LoopResult) -> AskResponse:
    return AskResponse(
        answer=result.answer,
        tool_call_id=result.tool_call_id,
        query_id=result.query_id,
        llm_calls=[
            LLMCallMetadata(
                llm_call_id=call.llm_call_id,
                model_id=call.model_id,
                usage=UsageMetadata(
                    input_tokens=call.input_tokens,
                    output_tokens=call.output_tokens,
                    total_tokens=call.input_tokens + call.output_tokens,
                ),
                latency_ms=call.latency_ms,
            )
            for call in result.llm_calls
        ],
        usage=UsageMetadata(
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            total_tokens=result.total_tokens,
        ),
        latency_ms=result.latency_ms,
        conversation_id=result.conversation_id,
        run_id=result.run_id,
    )


def create_ask_router(orchestration_loop: OrchestrationLoop) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.post("/ask", response_model=AskResponse)
    def ask(request: AskRequest) -> AskResponse:
        try:
            return _response_from_result(
                orchestration_loop.run(request.prompt, request.conversation_id)
            )
        except OrchestrationError as error:
            status_code = 503 if error.retryable else 502
            if error.code == "llm_configuration_error":
                status_code = 503
            elif error.code == "tool_validation_error":
                status_code = 422
            elif error.code == "conversation_not_found":
                status_code = 404
            raise HTTPException(
                status_code=status_code,
                detail={
                    "code": error.code,
                    "llm_call_id": error.llm_call_id,
                    "retryable": error.retryable,
                },
            ) from error
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "tool_validation_error",
                    "llm_call_id": "",
                    "retryable": False,
                },
            ) from error

    return router
