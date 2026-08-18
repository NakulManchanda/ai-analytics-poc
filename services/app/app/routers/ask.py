from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator

from app.llm import LLMClient, LLMResult


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


class AskResponse(BaseModel):
    answer: str
    model_id: str
    usage: UsageMetadata
    latency_ms: int


def create_ask_router(llm_client: LLMClient) -> APIRouter:
    router = APIRouter(prefix="/api")

    def get_llm_client() -> LLMClient:
        return llm_client

    @router.post("/ask", response_model=AskResponse)
    def ask(
        request: AskRequest,
        client: Annotated[LLMClient, Depends(get_llm_client)],
    ) -> AskResponse:
        result: LLMResult = client.ask(request.prompt)
        return AskResponse(
            answer=result.text,
            model_id=result.model_id,
            usage=UsageMetadata(
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            ),
            latency_ms=result.latency_ms,
        )

    return router
