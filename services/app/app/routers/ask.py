import logging
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.config import LLMConfigurationError
from app.llm import LLMClient, LLMProviderError, LLMResult, ToolProposalResult
from app.mcp_client import DatasetProfileMCPClient, MCPToolError

logger = logging.getLogger(__name__)
EXPECTED_TOOL_NAME = "get_dataset_profile"


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


class LLMCallMetadata(BaseModel):
    llm_call_id: str
    model_id: str
    usage: UsageMetadata
    latency_ms: int


class AskResponse(BaseModel):
    answer: str
    tool_call_id: str
    llm_calls: list[LLMCallMetadata]
    usage: UsageMetadata
    latency_ms: int


def usage_metadata(result: LLMResult | ToolProposalResult) -> UsageMetadata:
    return UsageMetadata(
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        total_tokens=result.input_tokens + result.output_tokens,
    )


def validate_profile_proposal(proposal: ToolProposalResult) -> bool:
    return proposal.name == EXPECTED_TOOL_NAME and proposal.arguments == {}


def create_ask_router(
    llm_client: LLMClient | None,
    llm_client_factory: Callable[[], LLMClient],
    mcp_client: DatasetProfileMCPClient | None,
    mcp_client_factory: Callable[[], DatasetProfileMCPClient],
    llm_call_id_factory: Callable[[], str],
    tool_call_id_factory: Callable[[], str],
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
        proposal_call_id = llm_call_id_factory()
        try:
            client = llm_client or llm_client_factory()
            proposal = client.propose_dataset_profile(request.prompt)
        except LLMConfigurationError as error:
            raise error_response(
                503, "llm_configuration_error", False, proposal_call_id
            ) from error
        except LLMProviderError as error:
            raise error_response(
                503 if error.retryable else 502,
                "llm_provider_error",
                error.retryable,
                proposal_call_id,
            ) from error

        logger.info(
            "m5_sequence phase=llm_tool_proposal llm_call_id=%s", proposal_call_id
        )
        if not validate_profile_proposal(proposal):
            raise error_response(422, "tool_validation_error", False, proposal_call_id)

        tool_call_id = tool_call_id_factory()
        try:
            profile = (mcp_client or mcp_client_factory()).get_dataset_profile()
        except MCPToolError as error:
            raise error_response(
                503 if error.retryable else 502,
                "mcp_tool_error",
                error.retryable,
                proposal_call_id,
            ) from error
        logger.info(
            "m5_sequence phase=mcp_dataset_profile tool_call_id=%s", tool_call_id
        )

        answer_call_id = llm_call_id_factory()
        try:
            answer_result = client.answer_with_dataset_profile(request.prompt, profile)
        except LLMProviderError as error:
            raise error_response(
                503 if error.retryable else 502,
                "llm_provider_error",
                error.retryable,
                answer_call_id,
            ) from error
        logger.info("m5_sequence phase=llm_final_answer llm_call_id=%s", answer_call_id)

        proposal_usage = usage_metadata(proposal)
        answer_usage = usage_metadata(answer_result)
        return AskResponse(
            answer=answer_result.text,
            tool_call_id=tool_call_id,
            llm_calls=[
                LLMCallMetadata(
                    llm_call_id=proposal_call_id,
                    model_id=proposal.model_id,
                    usage=proposal_usage,
                    latency_ms=proposal.latency_ms,
                ),
                LLMCallMetadata(
                    llm_call_id=answer_call_id,
                    model_id=answer_result.model_id,
                    usage=answer_usage,
                    latency_ms=answer_result.latency_ms,
                ),
            ],
            usage=UsageMetadata(
                input_tokens=proposal_usage.input_tokens + answer_usage.input_tokens,
                output_tokens=proposal_usage.output_tokens + answer_usage.output_tokens,
                total_tokens=proposal_usage.total_tokens + answer_usage.total_tokens,
            ),
            latency_ms=proposal.latency_ms + answer_result.latency_ms,
        )

    return router
