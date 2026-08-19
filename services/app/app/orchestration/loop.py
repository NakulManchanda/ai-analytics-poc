from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from app.config import DEFAULT_MODEL_ID, LLMConfigurationError
from app.llm import LLMClient, LLMProviderError, ToolProposalResult
from app.mcp_client import (
    ALLOWED_ANALYSES,
    DatasetProfileMCPClient,
    MCPToolError,
    sanitize_dataset_schema,
    sanitize_query_result,
)
from app.orchestration.budgets import (
    BudgetExceededError,
    BudgetTracker,
    ExecutionBudgets,
)
from app.state import (
    Conversation,
    InMemoryStateRepository,
    Message,
    Run,
    RunStep,
    StateRepository,
    generate_conversation_id,
    generate_llm_call_id,
    generate_message_id,
    generate_run_id,
    generate_step_id,
    generate_tool_call_id,
)

logger = logging.getLogger(__name__)
EXPECTED_TOOL_NAME = "query_taxi_data"

# Standard Bedrock Claude 3.5 Sonnet rate estimates: $0.003 / 1k input, $0.015 / 1k output
COST_PER_INPUT_TOKEN = 0.003 / 1_000.0
COST_PER_OUTPUT_TOKEN = 0.015 / 1_000.0


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * COST_PER_INPUT_TOKEN) + (
        output_tokens * COST_PER_OUTPUT_TOKEN
    )


@dataclass(frozen=True)
class LoopResult:
    """The result of an orchestration loop execution."""

    answer: str
    status: str
    run_id: str
    conversation_id: str
    steps: list[RunStep] = field(default_factory=list)
    tool_call_id: str | None = None
    query_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    latency_ms: int = 0
    failure_code: str | None = None


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


class OrchestrationLoop:
    """Application-owned bounded agent loop enforcing execution budgets and durable state."""

    def __init__(
        self,
        llm_client: LLMClient | None = None,
        llm_client_factory: Callable[[], LLMClient] | None = None,
        mcp_client: DatasetProfileMCPClient | None = None,
        mcp_client_factory: Callable[[], DatasetProfileMCPClient] | None = None,
        state_repository: StateRepository | None = None,
        budgets: ExecutionBudgets | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._llm_client_factory = llm_client_factory
        self._mcp_client = mcp_client
        self._mcp_client_factory = mcp_client_factory
        self._repo = state_repository or InMemoryStateRepository()
        self._budgets = budgets or ExecutionBudgets()

    def _get_llm_client(self) -> LLMClient:
        if self._llm_client is not None:
            return self._llm_client
        if self._llm_client_factory is not None:
            return self._llm_client_factory()
        raise LLMConfigurationError("No LLM client or factory configured")

    def _get_mcp_client(self) -> DatasetProfileMCPClient:
        if self._mcp_client is not None:
            return self._mcp_client
        if self._mcp_client_factory is not None:
            return self._mcp_client_factory()
        raise MCPToolError("No MCP client or factory configured", retryable=False)

    def run(
        self,
        prompt: str,
        conversation_id: str | None = None,
        budgets: ExecutionBudgets | None = None,
    ) -> LoopResult:
        active_budgets = budgets or self._budgets
        tracker = BudgetTracker(budgets=active_budgets)
        start_mono = time.monotonic()

        # 1. Initialize or load Conversation
        conv_id = conversation_id or generate_conversation_id()
        conv = self._repo.get_conversation(conv_id)
        if conv is None:
            conv = Conversation(conversation_id=conv_id)
            self._repo.create_conversation(conv)

        existing_messages = self._repo.list_messages(conv_id)
        next_seq = len(existing_messages) + 1

        # 2. Persist User Message
        user_msg_id = generate_message_id()
        self._repo.add_message(
            Message(
                message_id=user_msg_id,
                conversation_id=conv_id,
                sequence=next_seq,
                role="user",
                content=prompt,
            )
        )
        next_seq += 1

        # 3. Create Durable Run in progress
        run_id = generate_run_id()
        run = Run(
            run_id=run_id,
            conversation_id=conv_id,
            message_id=user_msg_id,
            status="in_progress",
            model=DEFAULT_MODEL_ID,
            prompt_version="m8.v1",
        )
        self._repo.create_run(run)

        executed_tool_signatures: set[str] = set()
        last_tool_call_id: str | None = None
        last_query_id: str | None = None
        steps: list[RunStep] = []
        step_seq = 1

        try:
            llm = self._get_llm_client()
            mcp = self._get_mcp_client()

            # Main bounded orchestration loop
            while True:
                tracker.record_iteration()

                # Step A: Load schema context
                try:
                    raw_schema = mcp.get_dataset_schema()
                    schema = sanitize_dataset_schema(raw_schema)
                except MCPToolError as err:
                    raise err

                # Step B: LLM Propose Taxi Query
                llm_call_id = generate_llm_call_id()
                call_start = time.monotonic()
                try:
                    proposal = llm.propose_taxi_query(prompt, schema)
                except (LLMProviderError, LLMConfigurationError) as err:
                    raise err
                call_latency_ms = int((time.monotonic() - call_start) * 1000)

                cost = estimate_cost(proposal.input_tokens, proposal.output_tokens)
                tracker.record_llm_call(
                    proposal.input_tokens,
                    proposal.output_tokens,
                    cost,
                )

                proposal_step = RunStep(
                    step_id=generate_step_id(),
                    run_id=run_id,
                    sequence=step_seq,
                    step_type="llm_proposal",
                    status="completed",
                    llm_call_id=llm_call_id,
                    input_summary=f"prompt: {prompt[:80]}",
                    output_summary=f"tool: {proposal.name}",
                    duration_ms=call_latency_ms,
                )
                self._repo.add_run_step(proposal_step)
                steps.append(proposal_step)
                step_seq += 1

                # Step C: Validate proposal & Check Repeated Calls
                query_request = parse_query_proposal(proposal)
                if query_request is None:
                    # Invalid tool proposal
                    invalid_step = RunStep(
                        step_id=generate_step_id(),
                        run_id=run_id,
                        sequence=step_seq,
                        step_type="validation_error",
                        status="failed",
                        input_summary=f"arguments: {proposal.arguments}",
                        output_summary="invalid tool arguments",
                    )
                    self._repo.add_run_step(invalid_step)
                    steps.append(invalid_step)
                    raise ValueError(f"Invalid tool proposal: {proposal.arguments}")

                analysis, limit = query_request
                tool_sig = f"{proposal.name}:{analysis}:{limit}"
                if tool_sig in executed_tool_signatures:
                    raise BudgetExceededError(
                        f"Repeated equivalent tool call detected: {tool_sig}",
                        {"limit": "repeated_tool_call", "signature": tool_sig},
                    )
                executed_tool_signatures.add(tool_sig)

                # Step D: Execute MCP Tool
                tool_call_id = generate_tool_call_id()
                last_tool_call_id = tool_call_id
                tool_start = time.monotonic()
                raw_query_result = mcp.query_taxi_data(analysis=analysis, limit=limit)
                query_result = sanitize_query_result(raw_query_result)
                tool_duration_ms = int((time.monotonic() - tool_start) * 1000)

                query_id_val = str(query_result.get("query_id", ""))
                last_query_id = query_id_val

                serialized_bytes = len(
                    json.dumps(query_result, separators=(",", ":")).encode("utf-8")
                )
                tracker.record_tool_call(result_bytes=serialized_bytes)

                tool_step = RunStep(
                    step_id=generate_step_id(),
                    run_id=run_id,
                    sequence=step_seq,
                    step_type="tool_call",
                    status="completed",
                    tool_name=proposal.name,
                    tool_call_id=tool_call_id,
                    query_id=query_id_val,
                    input_summary=f"analysis={analysis}, limit={limit}",
                    output_summary=(
                        f"rows={query_result.get('row_count', 0)}, bytes={serialized_bytes}"
                    ),
                    duration_ms=tool_duration_ms,
                )
                self._repo.add_run_step(tool_step)
                steps.append(tool_step)
                step_seq += 1

                # Step E: Answer with Query Result (Final LLM Step)
                answer_call_id = generate_llm_call_id()
                ans_start = time.monotonic()
                answer_result = llm.answer_with_query_result(prompt, query_result)
                ans_latency_ms = int((time.monotonic() - ans_start) * 1000)

                ans_cost = estimate_cost(
                    answer_result.input_tokens, answer_result.output_tokens
                )
                tracker.record_llm_call(
                    answer_result.input_tokens,
                    answer_result.output_tokens,
                    ans_cost,
                )

                answer_step = RunStep(
                    step_id=generate_step_id(),
                    run_id=run_id,
                    sequence=step_seq,
                    step_type="llm_final_answer",
                    status="completed",
                    llm_call_id=answer_call_id,
                    input_summary=f"query_id={query_id_val}",
                    output_summary=f"answer: {answer_result.text[:80]}",
                    duration_ms=ans_latency_ms,
                )
                self._repo.add_run_step(answer_step)
                steps.append(answer_step)

                # Persist assistant Message
                asst_msg_id = generate_message_id()
                self._repo.add_message(
                    Message(
                        message_id=asst_msg_id,
                        conversation_id=conv_id,
                        sequence=next_seq,
                        role="assistant",
                        content=answer_result.text,
                    )
                )

                # Update Run to completed
                total_latency_ms = int((time.monotonic() - start_mono) * 1000)
                completed_run = Run(
                    run_id=run_id,
                    conversation_id=conv_id,
                    message_id=user_msg_id,
                    status="completed",
                    model=DEFAULT_MODEL_ID,
                    prompt_version="m8.v1",
                    input_tokens=tracker.input_tokens,
                    output_tokens=tracker.output_tokens,
                    estimated_cost_usd=tracker.estimated_cost_usd,
                )
                self._repo.update_run(completed_run)

                return LoopResult(
                    answer=answer_result.text,
                    status="completed",
                    run_id=run_id,
                    conversation_id=conv_id,
                    steps=steps,
                    tool_call_id=last_tool_call_id,
                    query_id=last_query_id,
                    input_tokens=tracker.input_tokens,
                    output_tokens=tracker.output_tokens,
                    total_tokens=tracker.total_tokens,
                    estimated_cost_usd=tracker.estimated_cost_usd,
                    latency_ms=total_latency_ms,
                )

        except BudgetExceededError as err:
            logger.warning("Agent loop budget exceeded: %s", err.reason)
            total_latency_ms = int((time.monotonic() - start_mono) * 1000)
            exceeded_run = Run(
                run_id=run_id,
                conversation_id=conv_id,
                message_id=user_msg_id,
                status="budget_exceeded",
                model=DEFAULT_MODEL_ID,
                prompt_version="m8.v1",
                input_tokens=tracker.input_tokens,
                output_tokens=tracker.output_tokens,
                estimated_cost_usd=tracker.estimated_cost_usd,
                failure_code="budget_exceeded",
                metadata={"reason": err.reason, "details": err.details},
            )
            self._repo.update_run(exceeded_run)

            return LoopResult(
                answer="",
                status="budget_exceeded",
                run_id=run_id,
                conversation_id=conv_id,
                steps=steps,
                tool_call_id=last_tool_call_id,
                query_id=last_query_id,
                input_tokens=tracker.input_tokens,
                output_tokens=tracker.output_tokens,
                total_tokens=tracker.total_tokens,
                estimated_cost_usd=tracker.estimated_cost_usd,
                latency_ms=total_latency_ms,
                failure_code="budget_exceeded",
            )
