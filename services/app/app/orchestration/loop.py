from __future__ import annotations

import base64
import concurrent.futures
import contextvars
import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Tracer

from app.config import DEFAULT_MODEL_ID, LLMConfigurationError, VoiceSettings
from app.events import (
    EventPublisher,
    RunEvent,
    answer_audio_payload,
    context_reduced_payload,
    terminal_run_payload,
)
from app.llm import (
    LLMClient,
    LLMProviderError,
    LLMResult,
    ServeLLMClient,
    ToolProposalResult,
)
from app.prefix import PrefixPartition
from app.mcp_client import (
    ALLOWED_ANALYSES,
    DatasetProfileMCPClient,
    MCPToolError,
    sanitize_dataset_schema,
    sanitize_query_result,
)
from app.metrics import emit_run_metrics
from app.orchestration.budgets import (
    BudgetExceededError,
    BudgetTracker,
    ExecutionBudgets,
)
from app.orchestration.reducer import ContextReducer
from app.query_catalogue import lookup as catalogue_lookup
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
    utcnow_isoformat,
)

logger = logging.getLogger(__name__)
EXPECTED_TOOL_NAME = "query_taxi_data"
AVERAGE_METRICS_TOOL_NAME = "average_trip_metrics"
# Extended governed tools added by slice A. Reachable via the fixed query
# catalogue (D19) and, like the original tools, also via the model
# tool-proposal path. Their result envelopes carry extra descriptive fields
# (query_class, dimensions, code_dictionaries, ...) on top of the base
# bounded shape; the mcp_client adapter already runs each of these through
# the correct per-tool sanitizer (sanitize_describe_result /
# sanitize_governed_query_result) before the result reaches this loop, so no
# further app-side sanitization happens here.
EXTENDED_GOVERNED_TOOL_NAMES = frozenset(
    {"describe_taxi_dataset", "list_taxi_dimension_values", "aggregate_taxi_data"}
)


class RunCancelledError(Exception):
    """Raised when a run execution is aborted due to a cancellation request."""

    def __init__(
        self, partial_text: str = "", reason: str = "cancelled_by_user"
    ) -> None:
        super().__init__(reason)
        self.partial_text = partial_text
        self.reason = reason


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
    llm_calls: list[LLMCall] = field(default_factory=list)
    telemetry: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMCall:
    llm_call_id: str
    model_id: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    finish_reason: str | None = None
    configured_max_tokens: int | None = None
    ttft_ms: int | None = None
    first_visible_answer_ms: int | None = None
    reasoning_tokens: int | None = None
    reasoning_tokens_unavailable_reason: str | None = None
    visible_answer_tokens: int | None = None
    visible_answer_tokens_unavailable_reason: str | None = None
    cost_usd: float = 0.0
    cost_source: str = "bedrock_estimate"
    # Set when this call represents a failed provider call (e.g. no_tool_call,
    # invalid_tool_call) recorded for telemetry rather than a successful result.
    error_code: str | None = None
    # D19: "catalogue" when the tool proposal step was answered from the fixed
    # query catalogue (skipping the model call), "model" otherwise. Only set
    # meaningfully on the proposal LLMCall; unused ("model") on the answer call.
    tool_source: str = "model"

    def to_metadata(self) -> dict[str, Any]:
        return {
            "llm_call_id": self.llm_call_id,
            "model_id": self.model_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms,
            "finish_reason": self.finish_reason,
            "configured_max_tokens": self.configured_max_tokens,
            "ttft_ms": self.ttft_ms,
            "first_visible_answer_ms": self.first_visible_answer_ms,
            "reasoning_tokens": self.reasoning_tokens,
            "reasoning_tokens_unavailable_reason": self.reasoning_tokens_unavailable_reason,
            "visible_answer_tokens": self.visible_answer_tokens,
            "visible_answer_tokens_unavailable_reason": (
                self.visible_answer_tokens_unavailable_reason
            ),
            "cost_usd": self.cost_usd,
            "cost_source": self.cost_source,
            "error_code": self.error_code,
            "tool_source": self.tool_source,
        }


@dataclass(frozen=True)
class RunSubmission:
    """Carry-token from prepare_run to execute: identifies the pre-created durable state."""

    prompt: str
    conversation_id: str
    message_id: str
    run_id: str
    next_seq: int


class OrchestrationError(ValueError):
    """A controlled application-boundary failure from the orchestration loop."""

    def __init__(self, code: str, retryable: bool, llm_call_id: str, message: str):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.llm_call_id = llm_call_id
        self.message = message


def _is_str_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) for v in value)


def _parse_describe_taxi_dataset(arguments: object) -> dict[str, object] | None:
    args = arguments if arguments is not None else {}
    if not isinstance(args, Mapping) or set(args) - {"include_column_stats"}:
        return None
    include_column_stats = args.get("include_column_stats", False)
    if not isinstance(include_column_stats, bool):
        return None
    return {"include_column_stats": include_column_stats}


def _parse_list_taxi_dimension_values(arguments: object) -> dict[str, object] | None:
    if not isinstance(arguments, Mapping) or set(arguments) - {
        "dimension",
        "search",
        "limit",
    }:
        return None
    dimension = arguments.get("dimension")
    search = arguments.get("search")
    limit = arguments.get("limit", 20)
    if not isinstance(dimension, str) or not dimension:
        return None
    if search is not None and not isinstance(search, str):
        return None
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        return None
    return {"dimension": dimension, "search": search, "limit": limit}


def _parse_aggregate_taxi_data(arguments: object) -> dict[str, object] | None:
    if not isinstance(arguments, Mapping) or set(arguments) - {
        "dimensions",
        "measures",
        "filters",
        "order_by",
        "limit",
    }:
        return None
    dimensions = arguments.get("dimensions")
    measures = arguments.get("measures")
    filters = arguments.get("filters")
    order_by = arguments.get("order_by")
    limit = arguments.get("limit", 20)
    if not _is_str_list(dimensions) or not dimensions:
        return None
    if not _is_str_list(measures) or not measures:
        return None
    if filters is not None and not isinstance(filters, Mapping):
        return None
    if order_by is not None and not isinstance(order_by, Mapping):
        return None
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        return None
    return {
        "dimensions": dimensions,
        "measures": measures,
        "filters": dict(filters) if filters is not None else None,
        "order_by": dict(order_by) if order_by is not None else None,
        "limit": limit,
    }


def _parse_compare_taxi_segments(arguments: object) -> dict[str, object] | None:
    if not isinstance(arguments, Mapping) or set(arguments) - {
        "segment_dimension",
        "measures",
        "baseline_filters",
        "comparison_filters",
        "limit",
    }:
        return None
    segment_dimension = arguments.get("segment_dimension")
    measures = arguments.get("measures")
    baseline_filters = arguments.get("baseline_filters")
    comparison_filters = arguments.get("comparison_filters")
    limit = arguments.get("limit", 20)
    if not isinstance(segment_dimension, str) or not segment_dimension:
        return None
    if not _is_str_list(measures) or not measures:
        return None
    if not isinstance(baseline_filters, Mapping) or not isinstance(
        comparison_filters, Mapping
    ):
        return None
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        return None
    return {
        "segment_dimension": segment_dimension,
        "measures": measures,
        "baseline_filters": dict(baseline_filters),
        "comparison_filters": dict(comparison_filters),
        "limit": limit,
    }


_EXTENDED_TOOL_PARSERS: dict[str, Callable[[object], dict[str, object] | None]] = {
    "describe_taxi_dataset": _parse_describe_taxi_dataset,
    "list_taxi_dimension_values": _parse_list_taxi_dimension_values,
    "aggregate_taxi_data": _parse_aggregate_taxi_data,
    "compare_taxi_segments": _parse_compare_taxi_segments,
}


def parse_query_proposal(
    proposal: ToolProposalResult,
) -> tuple[str, dict[str, object]] | None:
    arguments = proposal.arguments
    if proposal.name in _EXTENDED_TOOL_PARSERS:
        parsed = _EXTENDED_TOOL_PARSERS[proposal.name](arguments)
        if parsed is None:
            return None
        return proposal.name, parsed
    if proposal.name == AVERAGE_METRICS_TOOL_NAME:
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, Mapping) or set(arguments) - {"region_name"}:
            return None
        region_name = arguments.get("region_name")
        if region_name is not None and (
            not isinstance(region_name, str)
            or not region_name.strip()
            or len(region_name) > 128
        ):
            return None
        return proposal.name, ({"region_name": region_name} if region_name else {})
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
    return proposal.name, {"analysis": analysis, "limit": limit}


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
        event_publisher: EventPublisher | None = None,
        context_reducer: ContextReducer | None = None,
        voice_settings: VoiceSettings | None = None,
        llm_call_id_factory: Callable[[], str] = generate_llm_call_id,
        tool_call_id_factory: Callable[[], str] = generate_tool_call_id,
        monotonic_factory: Callable[[], float] = time.monotonic,
        tracer: Tracer | None = None,
        agent_strategy: str = "manual",
    ) -> None:
        self._llm_client = llm_client
        self._llm_client_factory = llm_client_factory
        self._mcp_client = mcp_client
        self._mcp_client_factory = mcp_client_factory
        self._repo = state_repository or InMemoryStateRepository()
        self._budgets = budgets or ExecutionBudgets()
        self._publisher = event_publisher
        self._reducer = context_reducer or ContextReducer()
        self._voice_settings = voice_settings or VoiceSettings.from_environment()
        self._llm_call_id_factory = llm_call_id_factory
        self._tool_call_id_factory = tool_call_id_factory
        self._monotonic = monotonic_factory
        self._tracer = tracer or trace.get_tracer("ai_analytics_poc.orchestration")
        self._agent_strategy = agent_strategy
        # Initialize Polly client if voice synthesis is enabled
        if self._voice_settings.enabled and self._voice_settings.provider == "polly":
            from app.voice.polly import get_polly_client

            self._polly_client = get_polly_client()
        else:
            self._polly_client = None

    def _get_llm_client(self) -> LLMClient:
        if self._llm_client is not None:
            return self._llm_client
        if self._llm_client_factory is not None:
            return self._llm_client_factory()
        raise LLMConfigurationError("No LLM client or factory configured")

    def _configured_model_id(self) -> str:
        """Best-effort model id for the initial Run/span, before the guarded
        execute() path constructs (and validates) the real client. Any
        construction failure here is deferred to execute(), which already
        turns it into a proper OrchestrationError; this is display-only."""
        try:
            return self._get_llm_client().model_id
        except Exception:
            return DEFAULT_MODEL_ID

    def _get_mcp_client(self) -> DatasetProfileMCPClient:
        if self._mcp_client is not None:
            return self._mcp_client
        if self._mcp_client_factory is not None:
            return self._mcp_client_factory()
        raise MCPToolError("No MCP client or factory configured", retryable=False)

    def prepare_run(
        self,
        prompt: str,
        conversation_id: str | None = None,
    ) -> RunSubmission:
        """Durably create conversation, user message, and in-progress run, then publish
        run.received.  Returns a RunSubmission token for use by execute()."""

        # 1. Initialize or load Conversation
        conv_id = conversation_id or generate_conversation_id()
        conv = self._repo.get_conversation(conv_id)
        if conv is None:
            if conversation_id is not None:
                raise OrchestrationError(
                    "conversation_not_found",
                    False,
                    "",
                    f"Conversation {conv_id} not found",
                )
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
            model=self._configured_model_id(),
            prompt_version="m9.v1",
        )
        self._repo.create_run(run)

        # 4. Publish run.received before returning (so SSE clients see it before execute)
        if self._publisher is not None:
            self._publisher.publish(
                RunEvent(
                    event_type="run.received",
                    run_id=run_id,
                    conversation_id=conv_id,
                    sequence=1,
                    payload={"prompt_summary": prompt[:80], "status": "in_progress"},
                )
            )

        return RunSubmission(
            prompt=prompt,
            conversation_id=conv_id,
            message_id=user_msg_id,
            run_id=run_id,
            next_seq=next_seq,
        )

    def execute(
        self,
        submission: RunSubmission,
        budgets: ExecutionBudgets | None = None,
    ) -> LoopResult:
        """Execute the orchestration loop using state prepared by prepare_run()."""
        return self._execute_loop(
            prompt=submission.prompt,
            conv_id=submission.conversation_id,
            user_msg_id=submission.message_id,
            run_id=submission.run_id,
            next_seq=submission.next_seq,
            # run.received was already emitted by prepare_run; start evt_sequence at 1
            initial_evt_sequence=1,
            budgets=budgets,
        )

    def run(
        self,
        prompt: str,
        conversation_id: str | None = None,
        budgets: ExecutionBudgets | None = None,
    ) -> LoopResult:
        """Synchronous one-shot execution (preserves /api/ask compatibility)."""
        with self._tracer.start_as_current_span("ai.run") as span:
            submission = self.prepare_run(prompt, conversation_id)
            span.set_attributes(
                {
                    "ai.run_id": submission.run_id,
                    "ai.conversation_id": submission.conversation_id,
                    "ai.turn_type": "text",
                    "gen_ai.request.model": self._configured_model_id(),
                }
            )
            result = self.execute(submission, budgets=budgets)
            span.set_attribute("ai.status", result.status)
            return result

    def request_cancellation(self, run_id: str) -> Run:
        """Mark an active run as cancel_requested in durable state,
        set Redis flag, and emit run.cancel_requested."""
        run = self._repo.get_run(run_id)
        if run is None:
            raise OrchestrationError(
                "run_not_found", False, "", f"Run {run_id} not found"
            )
        if run.status in ("completed", "failed", "budget_exceeded", "cancelled"):
            raise OrchestrationError(
                "run_already_terminal",
                False,
                "",
                f"Run {run_id} is already terminal ({run.status})",
            )

        # Set Redis fast-cancellation flag if Redis is configured
        if self._publisher is not None and hasattr(self._publisher, "_get_client"):
            try:
                client = self._publisher._get_client()
                client.set(f"run:cancel:{run_id}", "1", ex=300)
            except Exception as error:
                logger.warning(
                    "Failed to set Redis cancel flag for %s: %s", run_id, error
                )

        # Update durable run status to cancel_requested
        updated_run = Run(
            run_id=run.run_id,
            conversation_id=run.conversation_id,
            message_id=run.message_id,
            status="cancel_requested",
            model=run.model,
            prompt_version=run.prompt_version,
            started_at=run.started_at,
            completed_at=run.completed_at,
            input_tokens=run.input_tokens,
            output_tokens=run.output_tokens,
            estimated_cost_usd=run.estimated_cost_usd,
            failure_code=run.failure_code,
            metadata=run.metadata,
        )
        self._repo.update_run(updated_run)

        # Emit live event
        if self._publisher is not None:
            self._publisher.publish(
                RunEvent(
                    event_type="run.cancel_requested",
                    run_id=run.run_id,
                    conversation_id=run.conversation_id,
                    sequence=0,
                    payload={"status": "cancel_requested"},
                )
            )
        return updated_run

    def is_cancelled(self, run_id: str) -> bool:
        """Check if a run has been flagged for cancellation via Redis or durable state."""
        if self._publisher is not None and hasattr(self._publisher, "_get_client"):
            try:
                client = self._publisher._get_client()
                if client.get(f"run:cancel:{run_id}") == "1":
                    return True
            except Exception as error:
                logger.warning(
                    "Failed to check Redis cancel flag for %s: %s", run_id, error
                )
        run = self._repo.get_run(run_id)
        return run is not None and run.status in ("cancel_requested", "cancelled")

    def _run_with_cancellation(
        self,
        func: Callable[..., Any],
        *args: Any,
        run_id: str,
        poll_interval: float = 0.05,
        partial_text: str = "",
        **kwargs: Any,
    ) -> Any:
        """Execute a blocking callable in a background thread with fast cooperative cancellation."""
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        try:
            context = contextvars.copy_context()
            future = executor.submit(context.run, func, *args, **kwargs)
            while not future.done():
                if self.is_cancelled(run_id):
                    future.cancel()
                    executor.shutdown(wait=False, cancel_futures=True)
                    raise RunCancelledError(partial_text=partial_text)
                time.sleep(poll_interval)
            return future.result()
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def _execute_loop(
        self,
        prompt: str,
        conv_id: str,
        user_msg_id: str,
        run_id: str,
        next_seq: int,
        initial_evt_sequence: int = 1,
        budgets: ExecutionBudgets | None = None,
    ) -> LoopResult:
        active_budgets = budgets or self._budgets
        tracker = BudgetTracker(budgets=active_budgets)
        start_mono = self._monotonic()
        evt_sequence = initial_evt_sequence

        # Re-fetch the in-progress run record for started_at reference
        run = self._repo.get_run(run_id)
        assert (
            run is not None
        ), f"Run {run_id} not found; prepare_run must be called first"

        def emit(
            event_type: str,
            payload: dict[str, Any] | None = None,
            step_id: str | None = None,
            llm_call_id: str | None = None,
            tool_call_id: str | None = None,
            query_id: str | None = None,
        ) -> RunEvent | None:
            nonlocal evt_sequence
            if self._publisher is None:
                return None
            evt = RunEvent(
                event_type=event_type,
                run_id=run_id,
                conversation_id=conv_id,
                sequence=evt_sequence,
                payload=payload or {},
                step_id=step_id,
                llm_call_id=llm_call_id,
                tool_call_id=tool_call_id,
                query_id=query_id,
            )
            self._publisher.publish(evt)
            evt_sequence += 1
            return evt

        steps: list[RunStep] = []
        step_seq = 1
        llm_calls: list[LLMCall] = []
        executed_tool_signatures: set[str] = set()
        last_tool_call_id: str | None = None
        last_query_id: str | None = None

        proposal_latency_ms: int | None = None
        tool_latency_ms: int | None = None
        final_answer_latency_ms: int | None = None
        final_answer_phase_reached = False
        final_answer_stream_started = False
        ttft_ms: int | None = None

        def telemetry(end_to_end_latency_ms: int) -> dict[str, Any]:
            if ttft_ms is not None:
                ttft_data = {
                    "available": True,
                    "latency_ms": ttft_ms,
                    "source": "provider_stream",
                }
            elif final_answer_stream_started:
                ttft_data = {
                    "available": False,
                    "reason": "provider_stream_returned_no_text_delta",
                }
            elif final_answer_phase_reached:
                ttft_data = {
                    "available": False,
                    "reason": "non_streaming_blocking",
                }
            else:
                ttft_data = {
                    "available": False,
                    "reason": "final_answer_not_started",
                }
            return {
                "end_to_end_latency_ms": end_to_end_latency_ms,
                "proposal_llm_latency_ms": proposal_latency_ms,
                "tool_latency_ms": tool_latency_ms,
                "final_answer_llm_latency_ms": final_answer_latency_ms,
                "ttft": ttft_data,
            }

        def check_cancellation(partial_text: str = "") -> None:
            if self.is_cancelled(run_id):
                raise RunCancelledError(partial_text=partial_text)

        # Whether the active LLM client is the owned, self-hosted vLLM gateway (serve
        # mode). Determined below once the client is obtained; defaults to False so the
        # exception paths below always have a well-defined value, even if client
        # construction itself fails.
        is_self_hosted = False
        configured_model_id = DEFAULT_MODEL_ID

        def call_cost(input_tokens: int, output_tokens: int) -> tuple[float, str]:
            # Serve mode runs on an owned, self-hosted vLLM gateway: it has no
            # per-token billing, so it is never charged at the Bedrock rate.
            if is_self_hosted:
                return 0.0, "self_hosted"
            return estimate_cost(input_tokens, output_tokens), "bedrock_estimate"

        def current_model_id() -> str:
            if llm_calls:
                return llm_calls[-1].model_id
            # Even before any call has completed (or if the only call fails
            # before producing an LLMCall), report the client's actually
            # configured model id rather than the Bedrock default -- this
            # matters most in serve mode, where the default would otherwise
            # mislabel a self-hosted failure as a Bedrock one.
            return configured_model_id

        def current_cost_source() -> str:
            if llm_calls:
                return llm_calls[-1].cost_source
            return "self_hosted" if is_self_hosted else "bedrock_estimate"

        try:
            check_cancellation()
            proposal_call_id = self._llm_call_id_factory()
            try:
                llm = self._get_llm_client()
                mcp = self._get_mcp_client()
                is_self_hosted = isinstance(llm, ServeLLMClient)
                configured_model_id = llm.model_id
            except LLMConfigurationError as err:
                raise OrchestrationError(
                    "llm_configuration_error", False, proposal_call_id, str(err)
                ) from err
            except MCPToolError as err:
                raise OrchestrationError(
                    "mcp_tool_error", err.retryable, proposal_call_id, str(err)
                ) from err

            # Main bounded orchestration loop
            while True:
                tracker.record_iteration()

                # Step A: Load schema context
                check_cancellation()
                emit("context.loading", {"resource": "dataset://nyc-taxi/schema"})
                try:
                    raw_schema = self._run_with_cancellation(
                        mcp.get_dataset_schema, run_id=run_id
                    )
                    schema = sanitize_dataset_schema(raw_schema)
                except MCPToolError as err:
                    raise OrchestrationError(
                        "mcp_tool_error", err.retryable, proposal_call_id, str(err)
                    ) from err

                # Step B: catalogue lookup (D19), else LLM Propose Taxi Query
                check_cancellation()
                catalogue_entry = catalogue_lookup(prompt)
                if catalogue_entry is not None:
                    llm_call_id = None
                    tool_name, tool_arguments = catalogue_entry
                    proposal_step = RunStep(
                        step_id=generate_step_id(),
                        run_id=run_id,
                        sequence=step_seq,
                        step_type="llm_proposal",
                        status="completed",
                        input_summary=f"prompt: {prompt[:80]}",
                        output_summary=f"tool: {tool_name}",
                        duration_ms=0,
                        metadata={"tool_source": "catalogue", "tool_name": tool_name},
                    )
                    self._repo.add_run_step(proposal_step)
                    steps.append(proposal_step)
                    step_seq += 1
                else:
                    llm_call_id = proposal_call_id
                    emit(
                        "llm.started",
                        {"llm_call_id": llm_call_id, "phase": "proposal"},
                    )
                    call_start = self._monotonic()
                    try:
                        proposal_kwargs: dict[str, Any] = (
                            {
                                "conversation_id": conv_id,
                                "repo": self._repo,
                                "current_message_id": user_msg_id,
                            }
                            if is_self_hosted
                            else {}
                        )
                        proposal = self._run_with_cancellation(
                            llm.propose_taxi_query,
                            prompt,
                            schema,
                            run_id=run_id,
                            **proposal_kwargs,
                        )
                    except LLMConfigurationError as err:
                        raise OrchestrationError(
                            "llm_configuration_error", False, llm_call_id, str(err)
                        ) from err
                    except LLMProviderError as err:
                        if err.model_id is not None:
                            # The provider returned a response (e.g. no_tool_call,
                            # invalid_tool_call) before failing our validation, so
                            # real telemetry exists -- record it as a failed call
                            # instead of discarding it.
                            fail_cost, fail_cost_source = call_cost(
                                err.input_tokens or 0, err.output_tokens or 0
                            )
                            llm_calls.append(
                                LLMCall(
                                    llm_call_id=llm_call_id,
                                    model_id=err.model_id,
                                    input_tokens=err.input_tokens or 0,
                                    output_tokens=err.output_tokens or 0,
                                    latency_ms=err.latency_ms or 0,
                                    finish_reason=err.finish_reason,
                                    cost_usd=fail_cost,
                                    cost_source=fail_cost_source,
                                    error_code=err.code,
                                    tool_source="model",
                                )
                            )
                        raise OrchestrationError(
                            err.code, err.retryable, llm_call_id, str(err)
                        ) from err
                    call_latency_ms = int((self._monotonic() - call_start) * 1000)
                    proposal_latency_ms = call_latency_ms

                    cost, cost_source = call_cost(
                        proposal.input_tokens, proposal.output_tokens
                    )
                    tracker.record_llm_call(
                        proposal.input_tokens,
                        proposal.output_tokens,
                        cost,
                    )
                    llm_calls.append(
                        LLMCall(
                            llm_call_id=llm_call_id,
                            model_id=proposal.model_id,
                            input_tokens=proposal.input_tokens,
                            output_tokens=proposal.output_tokens,
                            latency_ms=proposal.latency_ms,
                            finish_reason=proposal.finish_reason,
                            configured_max_tokens=proposal.configured_max_tokens,
                            cost_usd=cost,
                            cost_source=cost_source,
                            tool_source="model",
                        )
                    )
                    emit(
                        "llm.completed",
                        {
                            "llm_call_id": llm_call_id,
                            "phase": "proposal",
                            "latency_ms": call_latency_ms,
                            "tokens": {
                                "input": proposal.input_tokens,
                                "output": proposal.output_tokens,
                            },
                        },
                        llm_call_id=llm_call_id,
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
                        metadata=llm_calls[-1].to_metadata(),
                    )
                    self._repo.add_run_step(proposal_step)
                    steps.append(proposal_step)
                    step_seq += 1

                    # Step C: Validate proposal & Check Repeated Calls
                    query_request = parse_query_proposal(proposal)
                    if query_request is None:
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
                        raise OrchestrationError(
                            "tool_validation_error",
                            False,
                            llm_call_id,
                            f"Invalid tool proposal: {proposal.arguments}",
                        )

                    tool_name, tool_arguments = query_request

                emit(
                    "tool.requested",
                    {"tool_name": tool_name, **tool_arguments},
                )

                tool_sig = f"{tool_name}:{json.dumps(tool_arguments, sort_keys=True)}"
                if tool_sig in executed_tool_signatures:
                    raise BudgetExceededError(
                        f"Repeated equivalent tool call detected: {tool_sig}",
                        {"limit": "repeated_tool_call", "signature": tool_sig},
                    )
                executed_tool_signatures.add(tool_sig)

                # Step D: Execute MCP Tool
                check_cancellation()
                tool_call_id = self._tool_call_id_factory()
                last_tool_call_id = tool_call_id
                emit(
                    "tool.started",
                    {"tool_call_id": tool_call_id, "tool_name": tool_name},
                )
                tool_start = self._monotonic()
                try:
                    if tool_name == AVERAGE_METRICS_TOOL_NAME:
                        raw_query_result = self._run_with_cancellation(
                            mcp.average_trip_metrics,
                            region_name=tool_arguments.get("region_name"),
                            run_id=run_id,
                        )
                        query_result = sanitize_query_result(raw_query_result)
                    elif tool_name == EXPECTED_TOOL_NAME:
                        raw_query_result = self._run_with_cancellation(
                            mcp.query_taxi_data,
                            analysis=str(tool_arguments["analysis"]),
                            limit=int(tool_arguments["limit"]),
                            run_id=run_id,
                        )
                        query_result = sanitize_query_result(raw_query_result)
                    elif tool_name == "compare_taxi_segments":
                        # The mcp_client adapter already runs the result
                        # through sanitize_governed_query_result, which
                        # legitimately keeps extra fields (query_class,
                        # segment_dimension, measures, ...) beyond the base
                        # 6-field row/column shape. Re-sanitizing here with
                        # sanitize_query_result would reject every real
                        # compare result, so we use the already-sanitized
                        # result as-is.
                        query_result = self._run_with_cancellation(
                            mcp.compare_taxi_segments,
                            run_id=run_id,
                            **tool_arguments,
                        )
                    elif tool_name in EXTENDED_GOVERNED_TOOL_NAMES:
                        # Catalogue-only governed tools (D19): describe/list/
                        # aggregate carry extra descriptive fields
                        # (query_class, dimensions, code_dictionaries, ...) on
                        # top of the base bounded shape. The mcp_client
                        # adapter already runs each of these through the
                        # correct per-tool sanitizer
                        # (sanitize_describe_result / sanitize_governed_query_result),
                        # so no further app-side sanitization is needed here.
                        query_result = self._run_with_cancellation(
                            getattr(mcp, tool_name),
                            run_id=run_id,
                            **tool_arguments,
                        )
                    else:
                        raise MCPToolError(
                            retryable=False,
                            message=f"Unknown governed tool '{tool_name}'",
                        )
                except MCPToolError as err:
                    fail_duration_ms = int((self._monotonic() - tool_start) * 1000)
                    error_msg = err.message or str(err)
                    emit(
                        "tool.failed",
                        {
                            "tool_call_id": tool_call_id,
                            "tool_name": tool_name,
                            "error": error_msg,
                            "duration_ms": fail_duration_ms,
                        },
                        tool_call_id=tool_call_id,
                    )
                    failed_tool_step = RunStep(
                        step_id=generate_step_id(),
                        run_id=run_id,
                        sequence=step_seq,
                        step_type="tool_call",
                        status="failed",
                        tool_name=tool_name,
                        tool_call_id=tool_call_id,
                        input_summary=json.dumps(tool_arguments, sort_keys=True),
                        output_summary=f"error: {error_msg}",
                        duration_ms=fail_duration_ms,
                    )
                    self._repo.add_run_step(failed_tool_step)
                    steps.append(failed_tool_step)
                    step_seq += 1
                    raise OrchestrationError(
                        "mcp_tool_error", err.retryable, llm_call_id, error_msg
                    ) from err
                tool_duration_ms = int((self._monotonic() - tool_start) * 1000)
                tool_latency_ms = tool_duration_ms

                query_id_val = str(query_result.get("query_id", ""))
                last_query_id = query_id_val

                serialized_bytes = len(
                    json.dumps(query_result, separators=(",", ":")).encode("utf-8")
                )
                tracker.record_tool_call(result_bytes=serialized_bytes)

                emit(
                    "tool.completed",
                    {
                        "tool_call_id": tool_call_id,
                        "query_id": query_id_val,
                        "row_count": query_result.get("row_count", 0),
                        "duration_ms": tool_duration_ms,
                    },
                    tool_call_id=tool_call_id,
                    query_id=query_id_val,
                )

                tool_step = RunStep(
                    step_id=generate_step_id(),
                    run_id=run_id,
                    sequence=step_seq,
                    step_type="tool_call",
                    status="completed",
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    query_id=query_id_val,
                    input_summary=json.dumps(tool_arguments, sort_keys=True),
                    output_summary=(
                        f"rows={query_result.get('row_count', 0)}, bytes={serialized_bytes}"
                    ),
                    duration_ms=tool_duration_ms,
                )
                self._repo.add_run_step(tool_step)
                steps.append(tool_step)
                step_seq += 1

                # Persist the tool observation as a durable conversation message so
                # the prefix renderer can reconstruct the real growing prefix from
                # stored history (D18). Additive only: existing user/assistant
                # message handling is untouched.
                tool_msg_id = generate_message_id()
                self._repo.add_message(
                    Message(
                        message_id=tool_msg_id,
                        conversation_id=conv_id,
                        sequence=next_seq,
                        role="tool",
                        content=json.dumps(
                            query_result, separators=(",", ":"), sort_keys=True
                        ),
                        metadata={"tool_name": tool_name, "query_id": query_id_val},
                    )
                )
                next_seq += 1

                # Step E: Reduce context and Answer
                check_cancellation()
                working_ctx = self._reducer.reduce(
                    current_prompt=prompt,
                    stored_messages=self._repo.list_messages(conv_id),
                    current_message_id=user_msg_id,
                    dataset_schema=schema,
                    tool_observations=[query_result],
                    budget_tracker=tracker,
                    budgets=active_budgets,
                )
                emit(
                    "context.reduced",
                    context_reduced_payload(
                        query_id_val,
                        query_result.get("row_count", 0),
                        working_ctx.to_dict(),
                    ),
                )
                context_step = RunStep(
                    step_id=generate_step_id(),
                    run_id=run_id,
                    sequence=step_seq,
                    step_type="context_reduced",
                    status="completed",
                    query_id=query_id_val,
                    output_summary="persisted working context",
                    metadata={
                        "row_count": query_result.get("row_count", 0),
                        "working_context": working_ctx.to_dict(),
                    },
                )
                self._repo.add_run_step(context_step)
                steps.append(context_step)
                step_seq += 1

                answer_call_id = self._llm_call_id_factory()
                final_answer_phase_reached = True
                check_cancellation()
                ans_start = self._monotonic()

                if self._agent_strategy == "crewai":
                    from app.orchestration.crewai_strategy import run_two_agent_answer

                    def _crew_invoke_model(
                        role: str, partition: PrefixPartition, agent_step: int
                    ) -> LLMResult:
                        nonlocal step_seq, final_answer_phase_reached
                        final_answer_phase_reached = True

                        # 1. Pre-call cancellation check
                        check_cancellation()

                        # 2. Pre-call budget check
                        if tracker.llm_call_count >= tracker.budgets.max_llm_calls:
                            raise BudgetExceededError(
                                "max_llm_calls",
                                {
                                    "limit": tracker.budgets.max_llm_calls,
                                    "current": tracker.llm_call_count,
                                },
                            )

                        # 3. Allocate call_id and emit llm.started
                        call_id = self._llm_call_id_factory()
                        phase_name = f"crewai_{role.lower()}"
                        emit(
                            "llm.started",
                            {"llm_call_id": call_id, "phase": phase_name},
                            llm_call_id=call_id,
                        )

                        # 4. Call provider
                        call_start = self._monotonic()
                        try:
                            if hasattr(llm, "execute_partitioned_call") and callable(
                                llm.execute_partitioned_call
                            ):
                                call_res = llm.execute_partitioned_call(
                                    partition=partition,
                                    conversation_id=conv_id,
                                    agent_step=agent_step,
                                )
                            else:
                                user_content = f"{partition.conversation_shared}\n\n{partition.unique_suffix}".strip()
                                prompt_text = f"{partition.global_shared}\n\n{user_content}".strip()
                                call_res = llm.ask(prompt_text)
                        except LLMConfigurationError as err:
                            raise OrchestrationError(
                                "llm_configuration_error", False, call_id, str(err)
                            ) from err
                        except LLMProviderError as err:
                            raise OrchestrationError(
                                err.code, err.retryable, call_id, str(err)
                            ) from err

                        call_latency_ms = int((self._monotonic() - call_start) * 1000)

                        # 5. Record cost and tracker
                        c_cost, c_cost_source = call_cost(
                            call_res.input_tokens, call_res.output_tokens
                        )
                        tracker.record_llm_call(
                            call_res.input_tokens,
                            call_res.output_tokens,
                            c_cost,
                        )

                        # 6. Append LLMCall
                        recorded_call = LLMCall(
                            llm_call_id=call_id,
                            model_id=call_res.model_id,
                            input_tokens=call_res.input_tokens,
                            output_tokens=call_res.output_tokens,
                            latency_ms=(
                                call_res.latency_ms
                                if call_res.latency_ms
                                else call_latency_ms
                            ),
                            finish_reason=call_res.finish_reason,
                            configured_max_tokens=call_res.configured_max_tokens,
                            ttft_ms=call_res.ttft_ms,
                            first_visible_answer_ms=call_res.first_visible_answer_ms,
                            reasoning_tokens=call_res.reasoning_tokens,
                            reasoning_tokens_unavailable_reason=(
                                call_res.reasoning_tokens_unavailable_reason
                            ),
                            visible_answer_tokens=call_res.visible_answer_tokens,
                            visible_answer_tokens_unavailable_reason=(
                                call_res.visible_answer_tokens_unavailable_reason
                            ),
                            cost_usd=c_cost,
                            cost_source=c_cost_source,
                        )
                        llm_calls.append(recorded_call)

                        # 7. Emit llm.completed
                        emit(
                            "llm.completed",
                            {
                                "llm_call_id": call_id,
                                "phase": phase_name,
                                "latency_ms": call_latency_ms,
                                "tokens": {
                                    "input": call_res.input_tokens,
                                    "output": call_res.output_tokens,
                                },
                            },
                            llm_call_id=call_id,
                        )

                        # 8. Persist RunStep immediately
                        step = RunStep(
                            step_id=generate_step_id(),
                            run_id=run_id,
                            sequence=step_seq,
                            step_type=f"crewai_{role.lower()}",
                            status="completed",
                            llm_call_id=call_id,
                            input_summary=f"role={role}, agent_step={agent_step}",
                            output_summary=f"output: {call_res.text[:80]}",
                            duration_ms=call_latency_ms,
                            metadata=recorded_call.to_metadata(),
                        )
                        self._repo.add_run_step(step)
                        steps.append(step)
                        step_seq += 1

                        # 9. Post-call cancellation check
                        check_cancellation(
                            call_res.text if role.lower() == "writer" else ""
                        )
                        return call_res

                    try:
                        crew_answer = run_two_agent_answer(
                            question=prompt,
                            governed_result=query_result,
                            invoke_model=_crew_invoke_model,
                            conversation_id=conv_id,
                            start_step=2,
                        )
                    except (
                        RunCancelledError,
                        BudgetExceededError,
                        OrchestrationError,
                    ):
                        raise
                    except RuntimeError as err:
                        last_call_id = (
                            llm_calls[-1].llm_call_id if llm_calls else answer_call_id
                        )
                        raise OrchestrationError(
                            "strategy_execution_error", False, last_call_id, str(err)
                        ) from err
                    except Exception as err:
                        last_call_id = (
                            llm_calls[-1].llm_call_id if llm_calls else answer_call_id
                        )
                        raise OrchestrationError(
                            "strategy_execution_error", False, last_call_id, str(err)
                        ) from err

                    ans_latency_ms = int((self._monotonic() - ans_start) * 1000)
                    final_answer_latency_ms = ans_latency_ms
                    final_answer_text = crew_answer.text

                    last_call_id = (
                        llm_calls[-1].llm_call_id if llm_calls else answer_call_id
                    )
                    emit(
                        "answer.completed",
                        {"answer": final_answer_text},
                        llm_call_id=last_call_id,
                    )

                    # Synthesize audio if voice output is enabled
                    if self._polly_client and self._voice_settings.enabled:
                        try:
                            audio_bytes = self._polly_client.synthesize_speech(
                                text=final_answer_text,
                                voice_name=self._voice_settings.voice_name,
                                language_code=self._voice_settings.language,
                                output_format="mp3",
                            )
                            if audio_bytes:
                                audio_base64 = base64.b64encode(audio_bytes).decode(
                                    "utf-8"
                                )
                                emit(
                                    "answer.audio",
                                    answer_audio_payload(
                                        audio_base64=audio_base64,
                                        voice_name=self._voice_settings.voice_name,
                                        format="mp3",
                                    ),
                                    llm_call_id=last_call_id,
                                )
                        except Exception as err:
                            logger.warning(f"Failed to synthesize audio: {err}")

                    # Persist assistant Message
                    asst_msg_id = generate_message_id()
                    self._repo.add_message(
                        Message(
                            message_id=asst_msg_id,
                            conversation_id=conv_id,
                            sequence=next_seq,
                            role="assistant",
                            content=final_answer_text,
                        )
                    )
                else:
                    emit(
                        "llm.started",
                        {"llm_call_id": answer_call_id, "phase": "final_answer"},
                    )
                    accumulated_answer_chunks: list[str] = []

                    def publish_answer_delta(
                        delta: str,
                        _start: float = ans_start,
                        _call_id: str = answer_call_id,
                        _chunks: list[str] = accumulated_answer_chunks,
                    ) -> None:
                        nonlocal ttft_ms
                        if ttft_ms is None:
                            ttft_ms = int((self._monotonic() - _start) * 1000)
                        _chunks.append(delta)
                        emit(
                            "answer.delta",
                            {"delta": delta},
                            llm_call_id=_call_id,
                        )
                        check_cancellation("".join(_chunks))

                    try:
                        stream_answer = getattr(
                            llm, "stream_answer_with_query_result", None
                        )
                        answer_kwargs: dict[str, Any] = (
                            {
                                "conversation_id": conv_id,
                                "repo": self._repo,
                                "current_message_id": user_msg_id,
                            }
                            if is_self_hosted
                            else {}
                        )
                        if callable(stream_answer):
                            final_answer_stream_started = True
                            answer_result = stream_answer(
                                prompt,
                                query_result,
                                publish_answer_delta,
                                **answer_kwargs,
                            )
                        else:
                            final_answer_stream_started = False
                            answer_result = llm.answer_with_query_result(
                                prompt, query_result, **answer_kwargs
                            )
                    except LLMConfigurationError as err:
                        raise OrchestrationError(
                            "llm_configuration_error", False, answer_call_id, str(err)
                        ) from err
                    except LLMProviderError as err:
                        raise OrchestrationError(
                            err.code, err.retryable, answer_call_id, str(err)
                        ) from err
                    ans_latency_ms = int((self._monotonic() - ans_start) * 1000)
                    final_answer_latency_ms = ans_latency_ms
                    final_answer_text = answer_result.text

                    emit(
                        "answer.completed",
                        {"answer": final_answer_text},
                        llm_call_id=answer_call_id,
                    )

                    # Synthesize audio if voice output is enabled
                    if self._polly_client and self._voice_settings.enabled:
                        try:
                            audio_bytes = self._polly_client.synthesize_speech(
                                text=final_answer_text,
                                voice_name=self._voice_settings.voice_name,
                                language_code=self._voice_settings.language,
                                output_format="mp3",
                            )
                            if audio_bytes:
                                audio_base64 = base64.b64encode(audio_bytes).decode(
                                    "utf-8"
                                )
                                emit(
                                    "answer.audio",
                                    answer_audio_payload(
                                        audio_base64=audio_base64,
                                        voice_name=self._voice_settings.voice_name,
                                        format="mp3",
                                    ),
                                    llm_call_id=answer_call_id,
                                )
                        except Exception as err:
                            logger.warning(f"Failed to synthesize audio: {err}")

                    ans_cost, ans_cost_source = call_cost(
                        answer_result.input_tokens, answer_result.output_tokens
                    )
                    tracker.record_llm_call(
                        answer_result.input_tokens,
                        answer_result.output_tokens,
                        ans_cost,
                    )
                    llm_calls.append(
                        LLMCall(
                            llm_call_id=answer_call_id,
                            model_id=answer_result.model_id,
                            input_tokens=answer_result.input_tokens,
                            output_tokens=answer_result.output_tokens,
                            latency_ms=answer_result.latency_ms,
                            finish_reason=answer_result.finish_reason,
                            configured_max_tokens=answer_result.configured_max_tokens,
                            ttft_ms=answer_result.ttft_ms,
                            first_visible_answer_ms=answer_result.first_visible_answer_ms,
                            reasoning_tokens=answer_result.reasoning_tokens,
                            reasoning_tokens_unavailable_reason=(
                                answer_result.reasoning_tokens_unavailable_reason
                            ),
                            visible_answer_tokens=answer_result.visible_answer_tokens,
                            visible_answer_tokens_unavailable_reason=(
                                answer_result.visible_answer_tokens_unavailable_reason
                            ),
                            cost_usd=ans_cost,
                            cost_source=ans_cost_source,
                        )
                    )
                    emit(
                        "llm.completed",
                        {
                            "llm_call_id": answer_call_id,
                            "phase": "final_answer",
                            "latency_ms": ans_latency_ms,
                            "tokens": {
                                "input": answer_result.input_tokens,
                                "output": answer_result.output_tokens,
                            },
                        },
                        llm_call_id=answer_call_id,
                    )

                    answer_step = RunStep(
                        step_id=generate_step_id(),
                        run_id=run_id,
                        sequence=step_seq,
                        step_type="llm_final_answer",
                        status="completed",
                        llm_call_id=answer_call_id,
                        input_summary=f"query_id={query_id_val}",
                        output_summary=f"answer: {final_answer_text[:80]}",
                        duration_ms=ans_latency_ms,
                        metadata=llm_calls[-1].to_metadata(),
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
                            content=final_answer_text,
                        )
                    )

                # Update Run to completed
                total_latency_ms = int((self._monotonic() - start_mono) * 1000)
                run_telemetry = telemetry(total_latency_ms)
                completed_run = Run(
                    run_id=run_id,
                    conversation_id=conv_id,
                    message_id=user_msg_id,
                    status="completed",
                    model=current_model_id(),
                    prompt_version="m9.v1",
                    started_at=run.started_at,
                    completed_at=utcnow_isoformat(),
                    input_tokens=tracker.input_tokens,
                    output_tokens=tracker.output_tokens,
                    estimated_cost_usd=tracker.estimated_cost_usd,
                    metadata={
                        "telemetry": run_telemetry,
                        "cost_source": current_cost_source(),
                        "llm_calls": [call.to_metadata() for call in llm_calls],
                    },
                )
                self._repo.update_run(completed_run)

                emit(
                    "run.completed",
                    terminal_run_payload(
                        status="completed",
                        input_tokens=tracker.input_tokens,
                        output_tokens=tracker.output_tokens,
                        estimated_cost_usd=tracker.estimated_cost_usd,
                        failure_code=None,
                        telemetry=run_telemetry,
                    ),
                )
                emit_run_metrics(
                    run_id=run_id,
                    conversation_id=conv_id,
                    milestone="v3.1",
                    model=current_model_id(),
                    turn_type="text",
                    status="completed",
                    telemetry=run_telemetry,
                    input_tokens=tracker.input_tokens,
                    output_tokens=tracker.output_tokens,
                    estimated_cost_usd=tracker.estimated_cost_usd,
                    cost_source=current_cost_source(),
                    llm_calls=[call.to_metadata() for call in llm_calls],
                )

                return LoopResult(
                    answer=final_answer_text,
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
                    latency_ms=sum(call.latency_ms for call in llm_calls),
                    llm_calls=llm_calls,
                    telemetry=run_telemetry,
                )

        except RunCancelledError as err:
            logger.info("Run %s cancelled by user during execution", run_id)
            total_latency_ms = int((self._monotonic() - start_mono) * 1000)
            run_telemetry = telemetry(total_latency_ms)

            if err.partial_text:
                asst_msg_id = generate_message_id()
                self._repo.add_message(
                    Message(
                        message_id=asst_msg_id,
                        conversation_id=conv_id,
                        sequence=next_seq,
                        role="assistant",
                        content=err.partial_text + " [interrupted]",
                        metadata={"interrupted": True},
                    )
                )

            cancelled_run = Run(
                run_id=run_id,
                conversation_id=conv_id,
                message_id=user_msg_id,
                status="cancelled",
                model=current_model_id(),
                prompt_version="m9.v1",
                started_at=run.started_at,
                completed_at=utcnow_isoformat(),
                input_tokens=tracker.input_tokens,
                output_tokens=tracker.output_tokens,
                estimated_cost_usd=tracker.estimated_cost_usd,
                failure_code="cancelled",
                metadata={
                    "reason": err.reason,
                    "partial_text": err.partial_text,
                    "telemetry": run_telemetry,
                    "cost_source": current_cost_source(),
                    "llm_calls": [call.to_metadata() for call in llm_calls],
                },
            )
            self._repo.update_run(cancelled_run)

            emit(
                "run.cancelled",
                terminal_run_payload(
                    status="cancelled",
                    input_tokens=tracker.input_tokens,
                    output_tokens=tracker.output_tokens,
                    estimated_cost_usd=tracker.estimated_cost_usd,
                    failure_code="cancelled",
                    telemetry=run_telemetry,
                    reason=err.reason,
                ),
            )
            emit_run_metrics(
                run_id=run_id,
                conversation_id=conv_id,
                milestone="v3.1",
                model=current_model_id(),
                turn_type="text",
                status="cancelled",
                telemetry=run_telemetry,
                input_tokens=tracker.input_tokens,
                output_tokens=tracker.output_tokens,
                estimated_cost_usd=tracker.estimated_cost_usd,
                cost_source=current_cost_source(),
                llm_calls=[call.to_metadata() for call in llm_calls],
            )

            return LoopResult(
                answer=err.partial_text,
                status="cancelled",
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
                failure_code="cancelled",
                llm_calls=llm_calls,
                telemetry=run_telemetry,
            )

        except BudgetExceededError as err:
            logger.warning("Agent loop budget exceeded: %s", err.reason)
            total_latency_ms = int((self._monotonic() - start_mono) * 1000)
            run_telemetry = telemetry(total_latency_ms)
            exceeded_run = Run(
                run_id=run_id,
                conversation_id=conv_id,
                message_id=user_msg_id,
                status="budget_exceeded",
                model=current_model_id(),
                prompt_version="m9.v1",
                started_at=run.started_at,
                completed_at=utcnow_isoformat(),
                input_tokens=tracker.input_tokens,
                output_tokens=tracker.output_tokens,
                estimated_cost_usd=tracker.estimated_cost_usd,
                failure_code="budget_exceeded",
                metadata={
                    "reason": err.reason,
                    "details": err.details,
                    "telemetry": run_telemetry,
                    "cost_source": current_cost_source(),
                    "llm_calls": [call.to_metadata() for call in llm_calls],
                },
            )
            self._repo.update_run(exceeded_run)

            emit(
                "run.budget_exceeded",
                terminal_run_payload(
                    status="budget_exceeded",
                    input_tokens=tracker.input_tokens,
                    output_tokens=tracker.output_tokens,
                    estimated_cost_usd=tracker.estimated_cost_usd,
                    failure_code="budget_exceeded",
                    telemetry=run_telemetry,
                    reason=err.reason,
                ),
            )
            emit_run_metrics(
                run_id=run_id,
                conversation_id=conv_id,
                milestone="v3.1",
                model=current_model_id(),
                turn_type="text",
                status="budget_exceeded",
                telemetry=run_telemetry,
                input_tokens=tracker.input_tokens,
                output_tokens=tracker.output_tokens,
                estimated_cost_usd=tracker.estimated_cost_usd,
                cost_source=current_cost_source(),
                llm_calls=[call.to_metadata() for call in llm_calls],
            )

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
                llm_calls=llm_calls,
                telemetry=run_telemetry,
            )
        except OrchestrationError as err:
            total_latency_ms = int((self._monotonic() - start_mono) * 1000)
            run_telemetry = telemetry(total_latency_ms)
            failed_run = Run(
                run_id=run_id,
                conversation_id=conv_id,
                message_id=user_msg_id,
                status="failed",
                model=current_model_id(),
                prompt_version="m9.v1",
                started_at=run.started_at,
                completed_at=utcnow_isoformat(),
                input_tokens=tracker.input_tokens,
                output_tokens=tracker.output_tokens,
                estimated_cost_usd=tracker.estimated_cost_usd,
                failure_code=err.code,
                metadata={
                    "error": str(err),
                    "retryable": err.retryable,
                    "telemetry": run_telemetry,
                    "cost_source": current_cost_source(),
                    "llm_calls": [call.to_metadata() for call in llm_calls],
                },
            )
            self._repo.update_run(failed_run)
            emit(
                "run.failed",
                terminal_run_payload(
                    status="failed",
                    input_tokens=tracker.input_tokens,
                    output_tokens=tracker.output_tokens,
                    estimated_cost_usd=tracker.estimated_cost_usd,
                    failure_code=err.code,
                    telemetry=run_telemetry,
                    retryable=err.retryable,
                    error=err.message or str(err),
                ),
                llm_call_id=err.llm_call_id or None,
            )
            emit_run_metrics(
                run_id=run_id,
                conversation_id=conv_id,
                milestone="v3.1",
                model=current_model_id(),
                turn_type="text",
                status="failed",
                telemetry=run_telemetry,
                input_tokens=tracker.input_tokens,
                output_tokens=tracker.output_tokens,
                estimated_cost_usd=tracker.estimated_cost_usd,
                cost_source=current_cost_source(),
                llm_calls=[call.to_metadata() for call in llm_calls],
            )
            raise
