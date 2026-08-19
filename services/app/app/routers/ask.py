import logging
from collections.abc import Callable, Mapping
from typing import Annotated

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.config import LLMConfigurationError
from app.llm import LLMClient, LLMProviderError, LLMResult, ToolProposalResult
from app.mcp_client import (
    ALLOWED_ANALYSES,
    DatasetProfileMCPClient,
    MCPToolError,
    sanitize_dataset_schema,
    sanitize_query_result,
)
from app.orchestration import (
    ExecutionBudgets,
)
from app.state import (
    StateRepository,
)

logger = logging.getLogger(__name__)
EXPECTED_TOOL_NAME = "query_taxi_data"


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
    tool_call_id: str
    query_id: str
    llm_calls: list[LLMCallMetadata]
    usage: UsageMetadata
    latency_ms: int
    conversation_id: str | None = None
    run_id: str | None = None


def usage_metadata(result: LLMResult | ToolProposalResult) -> UsageMetadata:
    return UsageMetadata(
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        total_tokens=result.input_tokens + result.output_tokens,
    )


def parse_query_proposal(proposal: ToolProposalResult) -> tuple[str, int] | None:
    arguments = proposal.arguments
    if (
        proposal.name != EXPECTED_TOOL_NAME
        or not isinstance(arguments, Mapping)
        or set(arguments) != {"analysis", "limit"}
    ):
        return None
    analysis = arguments.get("analysis")
    limit = arguments.get("limit")
    if (
        not isinstance(analysis, str)
        or analysis not in ALLOWED_ANALYSES
        or isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 20
    ):
        return None
    return analysis, limit


def create_ask_router(
    llm_client: LLMClient | None,
    llm_client_factory: Callable[[], LLMClient],
    mcp_client: DatasetProfileMCPClient | None,
    mcp_client_factory: Callable[[], DatasetProfileMCPClient],
    llm_call_id_factory: Callable[[], str],
    tool_call_id_factory: Callable[[], str],
    state_repository: StateRepository | None = None,
    budgets: ExecutionBudgets | None = None,
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

    @router.post(
        "/ask",
        response_model=AskResponse,
        response_model_exclude_none=True,
    )
    def ask(request: AskRequest) -> AskResponse:
        proposal_call_id = llm_call_id_factory()
        tool_client = mcp_client or mcp_client_factory()
        try:
            schema = sanitize_dataset_schema(tool_client.get_dataset_schema())
        except MCPToolError as error:
            raise error_response(
                503 if error.retryable else 502,
                "mcp_tool_error",
                error.retryable,
                proposal_call_id,
            ) from error
        try:
            client = llm_client or llm_client_factory()
            proposal = client.propose_taxi_query(request.prompt, schema)
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
            "m6_sequence phase=llm_query_proposal llm_call_id=%s", proposal_call_id
        )
        query_request = parse_query_proposal(proposal)
        if query_request is None:
            raise error_response(422, "tool_validation_error", False, proposal_call_id)
        analysis, limit = query_request

        tool_call_id = tool_call_id_factory()
        try:
            query_result = sanitize_query_result(
                tool_client.query_taxi_data(analysis=analysis, limit=limit)
            )
        except MCPToolError as error:
            raise error_response(
                503 if error.retryable else 502,
                "mcp_tool_error",
                error.retryable,
                proposal_call_id,
            ) from error
        logger.info(
            "m6_sequence phase=mcp_query tool_call_id=%s query_id=%s",
            tool_call_id,
            query_result["query_id"],
        )

        answer_call_id = llm_call_id_factory()
        try:
            answer_result = client.answer_with_query_result(
                request.prompt, query_result
            )
        except LLMProviderError as error:
            raise error_response(
                503 if error.retryable else 502,
                "llm_provider_error",
                error.retryable,
                answer_call_id,
            ) from error
        logger.info("m6_sequence phase=llm_final_answer llm_call_id=%s", answer_call_id)

        proposal_usage = usage_metadata(proposal)
        answer_usage = usage_metadata(answer_result)
        return AskResponse(
            answer=answer_result.text,
            tool_call_id=tool_call_id,
            query_id=str(query_result["query_id"]),
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
