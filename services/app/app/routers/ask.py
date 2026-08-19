from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.config import LLMConfigurationError
from app.llm import LLMClient, LLMProviderError, LLMResult


class AskRequest(BaseModel):
    prompt: Annotated[str, Field(min_length=1, max_length=4_000)]

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


class AskResponse(BaseModel):
    answer: str
    llm_call_id: str
    model_id: str
    usage: UsageMetadata
    latency_ms: int


def create_ask_router(
    llm_client: LLMClient | None,
    llm_client_factory: Callable[[], LLMClient],
    llm_call_id_factory: Callable[[], str],
) -> APIRouter:
    router = APIRouter(prefix="/api")

    def error_response(
        status_code: int, code: str, retryable: bool, llm_call_id: str
    ) -> HTTPException:
        return HTTPException(
            status_code=status_code,
            detail={
                "code": code,
                "llm_call_id": llm_call_id,
                "retryable": retryable,
            },
        )

    @router.post("/ask", response_model=AskResponse)
    def ask(request: AskRequest) -> AskResponse:
        llm_call_id = llm_call_id_factory()
        try:
            client = llm_client or llm_client_factory()
            result: LLMResult = client.ask(request.prompt)
        except LLMConfigurationError as error:
            raise error_response(
                503, "llm_configuration_error", False, llm_call_id
            ) from error
        except LLMProviderError as error:
            raise error_response(
                503 if error.retryable else 502,
                "llm_provider_error",
                error.retryable,
                llm_call_id,
            ) from error

        return AskResponse(
            answer=result.text,
            llm_call_id=llm_call_id,
            model_id=result.model_id,
            usage=UsageMetadata(
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                total_tokens=result.input_tokens + result.output_tokens,
            ),
            latency_ms=result.latency_ms,
        )

    return router
