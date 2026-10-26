import contextvars
import json
import logging
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol

import httpx
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from opentelemetry import propagate

from app.bedrock_budget import (
    BedrockBudgetExceededError,
    BedrockBudgetUnavailableError,
    DynamoDBBedrockBudget,
    monthly_limit_micro_usd,
)
from app.config import DEFAULT_MODEL_ID, Settings
from app.model_policy import (
    ModelPolicyRuntime,
    PolicyDeniedError,
    PricingTable,
    RunBudgetExceededError,
    current_call_context,
)
from app.model_policy.context import current_llm_call_id
from app.prefix import (
    PrefixPartition,
    build_ask_partition,
    build_dataset_profile_partition,
    build_query_answer_partition,
    build_query_proposal_partition,
    estimate_tokens,
)
from app.prefix_render import render_conversation_prompt
from app.prompts import (
    ANSWER_SINGLE_TURN,
    DATASET_PROFILE_SINGLE_TURN,
    PLANNER_SINGLE_TURN,
    PromptAttribution,
    PromptBundle,
)
from app.validation.proposal import (
    BOROUGHS,
    DIMENSIONS,
    MAX_ROWS,
    MEASURES,
    PAYMENT_TYPE_CODES,
    RATE_CODE_CODES,
    VENDOR_CODES,
    WEEK_PARTS,
    WEEKDAYS,
    classify_non_tool_outcome,
)

logger = logging.getLogger(__name__)

# 1-based physical proxy request number within one logical call (policy fallbacks
# issue several requests under one llm_call_id); set by LiteLLMClient._governed.
_PROXY_ATTEMPT: contextvars.ContextVar[int] = contextvars.ContextVar(
    "litellm_proxy_attempt", default=1
)

RETRYABLE_BEDROCK_ERROR_CODES = {
    "InternalServerException",
    "ModelNotReadyException",
    "ModelTimeoutException",
    "ServiceUnavailableException",
    "ThrottlingException",
}
BEDROCK_RUNTIME_CONFIG = Config(retries={"total_max_attempts": 1})

# Bedrock rejects a model-produced tool-use block it cannot parse; LiteLLM relays it as
# a 4xx (observed: 424). Only this category survives -- never the response body.
MALFORMED_TOOL_USE_MARKER = "Model produced invalid sequence as part of ToolUse"
_PROVIDER_BODY_PEEK_CHARS = 2048


class LLMProviderError(Exception):
    def __init__(
        self,
        retryable: bool,
        code: str = "llm_provider_error",
        *,
        model_id: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        latency_ms: int | None = None,
        finish_reason: str | None = None,
        prompt_attribution: PromptAttribution | None = None,
        provider_status: int | None = None,
        provider_call_id: str | None = None,
        attempts: int | None = None,
    ) -> None:
        super().__init__()
        self.retryable = retryable
        self.code = code
        # Telemetry from the failed call, when available, so callers can
        # still record an honest failed LLMCall instead of losing it.
        self.model_id = model_id
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.latency_ms = latency_ms
        self.finish_reason = finish_reason
        # Which prompt revision the failed call had rendered (set by the client).
        self.prompt_attribution = prompt_attribution
        # Safe provider-failure facts (never the response body): the real HTTP status,
        # the proxy's call id for log correlation, and how many requests were made.
        self.provider_status = provider_status
        self.provider_call_id = provider_call_id
        self.attempts = attempts


_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"


def split_thinking(text: str) -> tuple[str, str]:
    """Split raw model content into (reasoning_text, visible_text) by removing any
    ``<think>...</think>`` blocks. Used for the non-streaming path; the streaming path
    uses `ThinkingSplitter` below to handle tags split across chunks."""
    reasoning_parts: list[str] = []
    visible_parts: list[str] = []
    remaining = text
    while True:
        start = remaining.find(_THINK_OPEN)
        if start == -1:
            visible_parts.append(remaining)
            break
        visible_parts.append(remaining[:start])
        after_open = remaining[start + len(_THINK_OPEN) :]
        end = after_open.find(_THINK_CLOSE)
        if end == -1:
            # Unterminated <think> block: treat the remainder as reasoning.
            reasoning_parts.append(after_open)
            remaining = ""
            break
        reasoning_parts.append(after_open[:end])
        remaining = after_open[end + len(_THINK_CLOSE) :]
    return "".join(reasoning_parts), "".join(visible_parts)


def _usage_known(usage: Mapping[str, Any]) -> bool:
    return (
        usage.get("prompt_tokens") is not None
        and usage.get("completion_tokens") is not None
    )


class ThinkingSplitter:
    """Incrementally separates `<think>...</think>` reasoning from visible text across
    a stream of arbitrarily-sized chunks, holding back just enough trailing text to
    detect a tag split across a chunk boundary. Visible output from `feed()` is the
    only text ever safe to hand to a stream callback."""

    def __init__(self) -> None:
        self._in_think = False
        self._pending = ""
        self.reasoning_text = ""

    def feed(self, chunk: str) -> str:
        if not chunk:
            return ""
        self._pending += chunk
        visible_out: list[str] = []
        while True:
            tag = _THINK_CLOSE if self._in_think else _THINK_OPEN
            idx = self._pending.find(tag)
            if idx == -1:
                # Hold back enough trailing characters that a tag split across the
                # next chunk boundary can still be detected once it arrives.
                keep = len(tag) - 1
                if len(self._pending) > keep:
                    released = self._pending[: len(self._pending) - keep]
                    if self._in_think:
                        self.reasoning_text += released
                    else:
                        visible_out.append(released)
                    self._pending = self._pending[len(self._pending) - keep :]
                break
            released = self._pending[:idx]
            if self._in_think:
                self.reasoning_text += released
            else:
                visible_out.append(released)
            self._pending = self._pending[idx + len(tag) :]
            self._in_think = not self._in_think
        return "".join(visible_out)

    def flush(self) -> str:
        """Call once the stream ends. Returns any trailing visible text that was held
        back waiting for a possible split tag."""
        remainder = self._pending
        self._pending = ""
        if self._in_think:
            self.reasoning_text += remainder
            return ""
        return remainder


@dataclass(frozen=True)
class LLMResult:
    text: str
    model_id: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    finish_reason: str | None = None
    configured_max_tokens: int | None = None
    # Time to the first token of any kind (reasoning or visible), streaming only.
    ttft_ms: int | None = None
    ttft_unavailable_reason: str | None = None
    # Time to the first user-visible (non-<think>) delta, streaming only.
    first_visible_answer_ms: int | None = None
    reasoning_tokens: int | None = None
    reasoning_tokens_unavailable_reason: str | None = None
    visible_answer_tokens: int | None = None
    visible_answer_tokens_unavailable_reason: str | None = None
    # Policy/accounting (#170); all optional so existing constructors are unchanged.
    role: str | None = None  # classifier | planner | answer
    model_alias: str | None = None
    pricing_version: str | None = None
    cost_usd: float | None = None  # None when unknown, never 0 by default
    cost_kind: str | None = None  # estimated | billed | unknown
    # False when the provider returned no usage; token fields are then not real
    # counts and consumers must record them as null.
    usage_known: bool = True
    fallback_used: bool = False
    # Template id, version and SHA-256 of the prompt actually rendered for THIS call.
    prompt_attribution: PromptAttribution | None = None


@dataclass(frozen=True)
class ToolProposalResult:
    name: str
    arguments: object
    model_id: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    finish_reason: str | None = None
    configured_max_tokens: int | None = None
    role: str | None = None
    model_alias: str | None = None
    pricing_version: str | None = None
    cost_usd: float | None = None
    cost_kind: str | None = None
    usage_known: bool = True
    fallback_used: bool = False
    prompt_attribution: PromptAttribution | None = None
    outcome: str = "tool_call"
    decline_reason: str | None = None
    # Safe structural facts about hidden reasoning (#186): never its text.
    reasoning_tokens: int | None = None
    reasoning_present: bool | None = None
    reasoning_chars: int | None = None
    output_basis: str | None = None  # "content" | "reasoning_only" | "reasoning_length"


def _reasoning_facts(message: dict[str, Any], usage: dict[str, Any]) -> dict[str, Any]:
    """Structural facts about provider reasoning. Only presence, a count and a length
    are returned; the reasoning text itself is never read into any result."""
    chars = 0
    rc = message.get("reasoning_content")
    if isinstance(rc, str):
        chars = len(rc)
    blocks = message.get("thinking_blocks")
    has_blocks = isinstance(blocks, list) and len(blocks) > 0
    details = usage.get("completion_tokens_details")
    tokens = details.get("reasoning_tokens") if isinstance(details, dict) else None
    tokens = tokens if isinstance(tokens, int) else None
    present = chars > 0 or has_blocks or bool(tokens and tokens > 0)
    return {"present": present, "tokens": tokens, "chars": chars}


def _attributed(result: Any, attribution: PromptAttribution | None) -> Any:
    return replace(result, prompt_attribution=attribution)


def _single_turn(bundle: PromptBundle, **values: str) -> tuple[str, PromptAttribution]:
    """Render a one-message prompt and its per-call attribution."""
    text = bundle.render("message", **values)
    return text, bundle.attribute(text)


def _attribute_errors(
    call: Callable[[], Any], attribution: PromptAttribution | None
) -> Any:
    """Run ``call``; a provider failure carries the prompt revision it had rendered."""
    try:
        return _attributed(call(), attribution)
    except LLMProviderError as error:
        error.prompt_attribution = attribution
        raise


class LLMClient(Protocol):
    @property
    def model_id(self) -> str:
        """The model id this client is actually configured to call, so callers
        can record it (e.g. in the initial Run and OTel span) without guessing
        or falling back to an unrelated provider default."""
        ...

    def ask(self, prompt: str) -> LLMResult: ...

    def propose_dataset_profile(self, prompt: str) -> ToolProposalResult: ...

    def answer_with_dataset_profile(
        self, prompt: str, dataset_profile: Mapping[str, object]
    ) -> LLMResult: ...

    def propose_taxi_query(
        self, prompt: str, schema: Mapping[str, object]
    ) -> ToolProposalResult: ...

    def answer_with_query_result(
        self, prompt: str, query_result: Mapping[str, object]
    ) -> LLMResult: ...

    def execute_partitioned_call(
        self,
        partition: PrefixPartition,
        *,
        conversation_id: str | None = None,
        agent_step: int = 1,
    ) -> LLMResult: ...


TAXI_PROPOSAL_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "query_taxi_data",
        "description": "Run one fixed read-only analysis over the pinned NYC Taxi dataset.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "analysis": {
                    "type": "string",
                    "enum": [
                        "top_pickup_zones",
                        "trip_volume_by_hour",
                        "average_distance_by_weekday",
                    ],
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["analysis", "limit"],
            "additionalProperties": False,
        },
    },
    {
        "name": "average_trip_metrics",
        "description": (
            "Compare average trip distance and fare amount across governed "
            "pickup boroughs (Manhattan, Brooklyn, Queens, Bronx, "
            "Staten Island). Leave region_name empty to compare all "
            "boroughs, or provide exactly one single borough name."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "region_name": {
                    "type": "string",
                    "description": (
                        "Optional single borough name. "
                        "Omit to compare all boroughs across NYC."
                    ),
                    "enum": [
                        "Manhattan",
                        "Brooklyn",
                        "Queens",
                        "Bronx",
                        "Staten Island",
                    ],
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "describe_taxi_dataset",
        "description": (
            "Describe taxi dataset metadata, coverage dates, record counts, and schema columns."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_column_stats": {
                    "type": "boolean",
                    "description": (
                        "Whether to include summary statistics for columns. Defaults to false."
                    ),
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "list_taxi_dimension_values",
        "description": "List distinct values for an allowlisted taxi dataset dimension.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dimension": {
                    "type": "string",
                    "description": "Allowlisted dimension name to list distinct values for.",
                    "enum": sorted(DIMENSIONS),
                },
                "search": {
                    "type": "string",
                    "description": "Optional substring match filter for dimension values.",
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        f"Maximum number of distinct values to return (1-{MAX_ROWS}). "
                        "Defaults to 20."
                    ),
                    "minimum": 1,
                    "maximum": MAX_ROWS,
                },
            },
            "required": ["dimension"],
            "additionalProperties": False,
        },
    },
    {
        "name": "aggregate_taxi_data",
        "description": (
            "Run flexible governed aggregations over allowlisted dimensions "
            "and measures with optional filters."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "dimensions": {
                    "type": "array",
                    "description": "List of allowlisted dimensions to group by.",
                    "items": {"type": "string", "enum": sorted(DIMENSIONS)},
                    "minItems": 1,
                    "maxItems": 16,
                },
                "measures": {
                    "type": "array",
                    "description": "List of allowlisted measures to aggregate.",
                    "items": {"type": "string", "enum": sorted(MEASURES)},
                    "minItems": 1,
                    "maxItems": 16,
                },
                "filters": {
                    "type": "object",
                    "description": "Optional filter predicates.",
                    "properties": {
                        "start_timestamp": {"type": "string"},
                        "end_timestamp": {"type": "string"},
                        "pickup_hour_min": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 23,
                        },
                        "pickup_hour_max": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 23,
                        },
                        "pickup_weekday": {"type": "string", "enum": sorted(WEEKDAYS)},
                        "pickup_week_part": {
                            "type": "string",
                            "enum": sorted(WEEK_PARTS),
                        },
                        "pickup_zone": {"type": "string"},
                        "dropoff_zone": {"type": "string"},
                        "pickup_borough": {"type": "string", "enum": sorted(BOROUGHS)},
                        "dropoff_borough": {"type": "string", "enum": sorted(BOROUGHS)},
                        "payment_type": {
                            "type": "integer",
                            "enum": sorted(PAYMENT_TYPE_CODES),
                        },
                        "rate_code": {
                            "type": "integer",
                            "enum": sorted(RATE_CODE_CODES),
                        },
                        "vendor": {"type": "integer", "enum": sorted(VENDOR_CODES)},
                        "passenger_count_min": {"type": "integer", "minimum": 0},
                        "passenger_count_max": {"type": "integer", "minimum": 0},
                        "trip_distance_min": {"type": "number", "minimum": 0},
                        "trip_distance_max": {"type": "number", "minimum": 0},
                        "trip_duration_min": {"type": "number", "minimum": 0},
                        "trip_duration_max": {"type": "number", "minimum": 0},
                        "fare_amount_min": {"type": "number", "minimum": 0},
                        "fare_amount_max": {"type": "number", "minimum": 0},
                        "airport_trip": {"type": "boolean"},
                        "valid_records_only": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                },
                "order_by": {
                    "type": "object",
                    "description": "Optional order by clause.",
                    "properties": {
                        "key": {
                            "type": "string",
                            "enum": sorted(DIMENSIONS | MEASURES),
                        },
                        "direction": {"type": "string", "enum": ["asc", "desc"]},
                    },
                    "required": ["key"],
                    "additionalProperties": False,
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        f"Maximum number of rows to return (1-{MAX_ROWS}). Defaults to 20."
                    ),
                    "minimum": 1,
                    "maximum": MAX_ROWS,
                },
            },
            "required": ["dimensions", "measures"],
            "additionalProperties": False,
        },
    },
    {
        "name": "compare_taxi_segments",
        "description": (
            "Compare a baseline and a comparison population. comparison_mode "
            "'by_dimension' (default) aligns them per segment of a shared "
            "segment_dimension and needs the filters to differ on something other "
            "than that dimension; 'whole_segment' compares the two filtered "
            "populations as wholes (use it when the two populations are defined by "
            "the same field, e.g. Manhattan vs Queens) and must omit segment_dimension."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "segment_dimension": {
                    "type": "string",
                    "description": (
                        "Shared dimension along which to compare segments. Required "
                        "for by_dimension; must be omitted for whole_segment."
                    ),
                    "enum": sorted(DIMENSIONS),
                },
                "comparison_mode": {
                    "type": "string",
                    "description": (
                        "'by_dimension' (default) or 'whole_segment'. Chosen "
                        "explicitly; never inferred by the server."
                    ),
                    "enum": ["by_dimension", "whole_segment"],
                },
                "measures": {
                    "type": "array",
                    "description": "List of allowlisted measures to compare.",
                    "items": {"type": "string", "enum": sorted(MEASURES)},
                    "minItems": 1,
                    "maxItems": 16,
                },
                "baseline_filters": {
                    "type": "object",
                    "description": "Filters defining the baseline segment.",
                    "properties": {
                        "start_timestamp": {"type": "string"},
                        "end_timestamp": {"type": "string"},
                        "pickup_hour_min": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 23,
                        },
                        "pickup_hour_max": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 23,
                        },
                        "pickup_weekday": {"type": "string", "enum": sorted(WEEKDAYS)},
                        "pickup_week_part": {
                            "type": "string",
                            "enum": sorted(WEEK_PARTS),
                        },
                        "pickup_zone": {"type": "string"},
                        "dropoff_zone": {"type": "string"},
                        "pickup_borough": {"type": "string", "enum": sorted(BOROUGHS)},
                        "dropoff_borough": {"type": "string", "enum": sorted(BOROUGHS)},
                        "payment_type": {
                            "type": "integer",
                            "enum": sorted(PAYMENT_TYPE_CODES),
                        },
                        "rate_code": {
                            "type": "integer",
                            "enum": sorted(RATE_CODE_CODES),
                        },
                        "vendor": {"type": "integer", "enum": sorted(VENDOR_CODES)},
                        "passenger_count_min": {"type": "integer", "minimum": 0},
                        "passenger_count_max": {"type": "integer", "minimum": 0},
                        "trip_distance_min": {"type": "number", "minimum": 0},
                        "trip_distance_max": {"type": "number", "minimum": 0},
                        "trip_duration_min": {"type": "number", "minimum": 0},
                        "trip_duration_max": {"type": "number", "minimum": 0},
                        "fare_amount_min": {"type": "number", "minimum": 0},
                        "fare_amount_max": {"type": "number", "minimum": 0},
                        "airport_trip": {"type": "boolean"},
                        "valid_records_only": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                },
                "comparison_filters": {
                    "type": "object",
                    "description": "Filters defining the comparison segment.",
                    "properties": {
                        "start_timestamp": {"type": "string"},
                        "end_timestamp": {"type": "string"},
                        "pickup_hour_min": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 23,
                        },
                        "pickup_hour_max": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 23,
                        },
                        "pickup_weekday": {"type": "string", "enum": sorted(WEEKDAYS)},
                        "pickup_week_part": {
                            "type": "string",
                            "enum": sorted(WEEK_PARTS),
                        },
                        "pickup_zone": {"type": "string"},
                        "dropoff_zone": {"type": "string"},
                        "pickup_borough": {"type": "string", "enum": sorted(BOROUGHS)},
                        "dropoff_borough": {"type": "string", "enum": sorted(BOROUGHS)},
                        "payment_type": {
                            "type": "integer",
                            "enum": sorted(PAYMENT_TYPE_CODES),
                        },
                        "rate_code": {
                            "type": "integer",
                            "enum": sorted(RATE_CODE_CODES),
                        },
                        "vendor": {"type": "integer", "enum": sorted(VENDOR_CODES)},
                        "passenger_count_min": {"type": "integer", "minimum": 0},
                        "passenger_count_max": {"type": "integer", "minimum": 0},
                        "trip_distance_min": {"type": "number", "minimum": 0},
                        "trip_distance_max": {"type": "number", "minimum": 0},
                        "trip_duration_min": {"type": "number", "minimum": 0},
                        "trip_duration_max": {"type": "number", "minimum": 0},
                        "fare_amount_min": {"type": "number", "minimum": 0},
                        "fare_amount_max": {"type": "number", "minimum": 0},
                        "airport_trip": {"type": "boolean"},
                        "valid_records_only": {"type": "boolean"},
                    },
                    "additionalProperties": False,
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        f"Maximum number of rows to return (1-{MAX_ROWS}). Defaults to 20."
                    ),
                    "minimum": 1,
                    "maximum": MAX_ROWS,
                },
            },
            "required": [
                "measures",
                "baseline_filters",
                "comparison_filters",
            ],
            "additionalProperties": False,
        },
    },
]


def _taxi_proposal(
    prompt: str, schema: Mapping[str, object]
) -> tuple[str, PromptAttribution]:
    schema_json = json.dumps(schema, separators=(",", ":"), allow_nan=False)
    return _single_turn(PLANNER_SINGLE_TURN, schema_json=schema_json, question=prompt)


def taxi_proposal_prompt(prompt: str, schema: Mapping[str, object]) -> str:
    return _taxi_proposal(prompt, schema)[0]


class LocalFakeLLMClient:
    """Deterministic local-only client used by the Compose M5 smoke path."""

    @property
    def model_id(self) -> str:
        return DEFAULT_MODEL_ID

    def ask(self, prompt: str) -> LLMResult:
        return self._result(prompt, "Local fake answer.")

    def propose_dataset_profile(self, prompt: str) -> ToolProposalResult:
        return ToolProposalResult(
            name="get_dataset_profile",
            arguments={},
            model_id=DEFAULT_MODEL_ID,
            input_tokens=max(1, len(prompt.split())),
            output_tokens=1,
            latency_ms=0,
        )

    def answer_with_dataset_profile(
        self, prompt: str, dataset_profile: Mapping[str, object]
    ) -> LLMResult:
        row_count = dataset_profile.get("row_count")
        if isinstance(row_count, bool) or not isinstance(row_count, int):
            raise LLMProviderError(retryable=False)
        return self._result(prompt, f"The profile contains {row_count} taxi trips.")

    def propose_taxi_query(
        self, prompt: str, schema: Mapping[str, object]
    ) -> ToolProposalResult:
        if not isinstance(schema.get("columns"), list):
            raise LLMProviderError(retryable=False)
        normalized_prompt = prompt.lower()
        if any(
            w in normalized_prompt
            for w in ("why", "predict", "driver", "weather", "delete", "cannot")
        ):
            return ToolProposalResult(
                name="",
                arguments=None,
                outcome="unsupported",
                decline_reason=(
                    "I cannot determine that: the dataset does not contain this information."
                ),
                model_id=DEFAULT_MODEL_ID,
                input_tokens=max(1, len(prompt.split())),
                output_tokens=12,
                latency_ms=0,
            )
        if "fare" in normalized_prompt and (
            "borough" in normalized_prompt or "region" in normalized_prompt
        ):
            name = "average_trip_metrics"
            arguments: dict[str, object] = {}
        else:
            if "hour" in normalized_prompt:
                analysis = "trip_volume_by_hour"
            elif "weekday" in normalized_prompt or "distance" in normalized_prompt:
                analysis = "average_distance_by_weekday"
            else:
                analysis = "top_pickup_zones"
            name = "query_taxi_data"
            arguments = {"analysis": analysis, "limit": 5}
        return ToolProposalResult(
            name=name,
            arguments=arguments,
            outcome="tool_call",
            model_id=DEFAULT_MODEL_ID,
            input_tokens=max(1, len(prompt.split())),
            output_tokens=4,
            latency_ms=0,
        )

    def answer_with_query_result(
        self, prompt: str, query_result: Mapping[str, object]
    ) -> LLMResult:
        columns = query_result.get("columns")
        rows = query_result.get("rows")
        if not isinstance(columns, list) or not isinstance(rows, list) or not rows:
            return self._result(prompt, "The governed query returned no rows.")
        first_row = rows[0]
        if not isinstance(first_row, list):
            raise LLMProviderError(retryable=False)
        if columns == [
            "region_name",
            "trip_count",
            "average_trip_distance",
            "average_fare_amount",
        ]:
            if (
                len(first_row) != 4
                or not isinstance(first_row[0], str)
                or isinstance(first_row[1], bool)
                or not isinstance(first_row[1], int)
                or not isinstance(first_row[2], (int, float))
                or isinstance(first_row[2], bool)
                or not isinstance(first_row[3], (int, float))
                or isinstance(first_row[3], bool)
            ):
                raise LLMProviderError(retryable=False)
            text = (
                f"{first_row[0]} averages {float(first_row[2]):.2f} miles and "
                f"${float(first_row[3]):.2f} in fare across {first_row[1]} trips."
            )
            return self._result(prompt, text)
        if len(first_row) != 2:
            raise LLMProviderError(retryable=False)
        if columns == ["pickup_zone", "trip_count"]:
            text = f"{first_row[0]} has the most pickups with {first_row[1]} trips."
        elif columns == ["pickup_hour", "trip_count"]:
            text = (
                f"Hour {first_row[0]} has the highest volume with {first_row[1]} trips."
            )
        else:
            text = (
                f"{first_row[0]} has an average trip distance of {first_row[1]} miles."
            )
        return self._result(prompt, text)

    def execute_partitioned_call(
        self,
        partition: PrefixPartition,
        *,
        conversation_id: str | None = None,
        agent_step: int = 1,
    ) -> LLMResult:
        user_content = (
            f"{partition.conversation_shared}\n\n{partition.unique_suffix}".strip()
        )
        if agent_step == 2 or "research" in partition.unique_suffix.lower():
            text = "Research summary: The governed query returned 2 rows."
        else:
            text = "Crew answer 2"
        return self._result(user_content, text)

    def _result(self, prompt: str, text: str) -> LLMResult:
        return LLMResult(
            text=text,
            model_id=DEFAULT_MODEL_ID,
            input_tokens=max(1, len(prompt.split())),
            output_tokens=max(1, len(text.split())),
            latency_ms=0,
        )


class BedrockLLMClient:
    def __init__(
        self,
        model_id: str,
        region_name: str | None = None,
        runtime_client: Any | None = None,
        budget: Any | None = None,
    ) -> None:
        self._model_id = model_id
        self._region_name = region_name
        self._runtime_client = runtime_client
        self._budget = budget

    @property
    def model_id(self) -> str:
        return self._model_id

    def ask(self, prompt: str) -> LLMResult:
        response = self._converse(
            messages=[{"role": "user", "content": [{"text": prompt}]}],
        )
        return self._as_llm_result(response)

    def propose_dataset_profile(self, prompt: str) -> ToolProposalResult:
        response = self._converse(
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            tool_config={
                "tools": [
                    {
                        "toolSpec": {
                            "name": "get_dataset_profile",
                            "description": (
                                "Return the fixed profile for the pinned NYC Taxi dataset."
                            ),
                            "inputSchema": {
                                "json": {
                                    "type": "object",
                                    "properties": {},
                                    "additionalProperties": False,
                                }
                            },
                        }
                    }
                ],
                "toolChoice": {"tool": {"name": "get_dataset_profile"}},
            },
        )
        content = response["output"]["message"]["content"]
        tool_uses = [block["toolUse"] for block in content if "toolUse" in block]
        if len(tool_uses) != 1:
            name: object = ""
            arguments: object = None
        else:
            name = tool_uses[0].get("name", "")
            arguments = tool_uses[0].get("input")
        return ToolProposalResult(
            name=name if isinstance(name, str) else "",
            arguments=arguments,
            model_id=response.get("modelId", self._model_id),
            input_tokens=response["usage"]["inputTokens"],
            output_tokens=response["usage"]["outputTokens"],
            latency_ms=response["metrics"]["latencyMs"],
        )

    def answer_with_dataset_profile(
        self, prompt: str, dataset_profile: Mapping[str, object]
    ) -> LLMResult:
        profile_json = json.dumps(
            dataset_profile, separators=(",", ":"), allow_nan=False
        )
        text, attribution = _single_turn(
            DATASET_PROFILE_SINGLE_TURN, question=prompt, profile_json=profile_json
        )
        return _attribute_errors(
            lambda: self._as_llm_result(
                self._converse(
                    messages=[{"role": "user", "content": [{"text": text}]}],
                )
            ),
            attribution,
        )

    def propose_taxi_query(
        self, prompt: str, schema: Mapping[str, object]
    ) -> ToolProposalResult:
        text, attribution = _taxi_proposal(prompt, schema)
        try:
            response = self._converse(
                messages=[{"role": "user", "content": [{"text": text}]}],
                tool_config={
                    "tools": [
                        {
                            "toolSpec": {
                                "name": spec["name"],
                                "description": spec["description"],
                                "inputSchema": {"json": spec["inputSchema"]},
                            }
                        }
                        for spec in TAXI_PROPOSAL_TOOL_SPECS
                    ],
                    "toolChoice": {"auto": {}},
                },
            )
        except LLMProviderError as error:
            error.prompt_attribution = attribution
            raise
        content = response["output"]["message"]["content"]
        tool_uses = [block["toolUse"] for block in content if "toolUse" in block]
        if len(tool_uses) == 1:
            name = tool_uses[0].get("name", "")
            arguments = tool_uses[0].get("input")
            outcome = "tool_call"
            decline_reason = None
        elif len(tool_uses) > 1:
            # One call per planner step: never substitute a different operation.
            raise LLMProviderError(
                retryable=False,
                code="multiple_tool_calls",
                model_id=response.get("modelId", self._model_id),
                input_tokens=response["usage"]["inputTokens"],
                output_tokens=response["usage"]["outputTokens"],
                prompt_attribution=attribution,
            )
        else:
            name = ""
            arguments = None
            decline_reason = "".join(
                block.get("text", "") for block in content if "text" in block
            ).strip()
            outcome = classify_non_tool_outcome(decline_reason, prompt)
        return ToolProposalResult(
            name=name if isinstance(name, str) else "",
            arguments=arguments,
            outcome=outcome,
            decline_reason=decline_reason,
            model_id=response.get("modelId", self._model_id),
            input_tokens=response["usage"]["inputTokens"],
            output_tokens=response["usage"]["outputTokens"],
            latency_ms=response["metrics"]["latencyMs"],
            prompt_attribution=attribution,
        )

    def answer_with_query_result(
        self, prompt: str, query_result: Mapping[str, object]
    ) -> LLMResult:
        result_json = json.dumps(query_result, separators=(",", ":"), allow_nan=False)
        text, attribution = _single_turn(
            ANSWER_SINGLE_TURN, question=prompt, result_json=result_json
        )
        return _attribute_errors(
            lambda: self._as_llm_result(
                self._converse(
                    messages=[{"role": "user", "content": [{"text": text}]}],
                )
            ),
            attribution,
        )

    def execute_partitioned_call(
        self,
        partition: PrefixPartition,
        *,
        conversation_id: str | None = None,
        agent_step: int = 1,
    ) -> LLMResult:
        user_content = (
            f"{partition.conversation_shared}\n\n{partition.unique_suffix}".strip()
        )
        system_blocks = (
            [{"text": partition.global_shared}] if partition.global_shared else None
        )
        return _attribute_errors(
            lambda: self._as_llm_result(
                self._converse(
                    messages=[{"role": "user", "content": [{"text": user_content}]}],
                    system=system_blocks,
                    max_tokens=1024,
                )
            ),
            partition.attribution,
        )

    def stream_answer_with_query_result(
        self,
        prompt: str,
        query_result: Mapping[str, object],
        on_delta: Callable[[str], None],
    ) -> LLMResult:
        result_json = json.dumps(query_result, separators=(",", ":"), allow_nan=False)
        text, attribution = _single_turn(
            ANSWER_SINGLE_TURN, question=prompt, result_json=result_json
        )
        request = {
            "modelId": self._model_id,
            "messages": [{"role": "user", "content": [{"text": text}]}],
            "inferenceConfig": {"maxTokens": 128, "temperature": 0.0},
        }
        return _attribute_errors(
            lambda: self._stream_request(request, on_delta), attribution
        )

    def _stream_request(
        self, request: dict[str, Any], on_delta: Callable[[str], None]
    ) -> LLMResult:
        chunks: list[str] = []
        metadata: Mapping[str, Any] | None = None
        try:
            self._reserve_budget()
            response = self._get_runtime_client().converse_stream(**request)
            for event in response["stream"]:
                content_delta = event.get("contentBlockDelta")
                if content_delta is not None:
                    text = content_delta.get("delta", {}).get("text")
                    if isinstance(text, str) and text:
                        chunks.append(text)
                        on_delta(text)
                if "metadata" in event:
                    metadata = event["metadata"]
        except ClientError as error:
            error_code = error.response.get("Error", {}).get("Code", "")
            raise LLMProviderError(
                retryable=error_code in RETRYABLE_BEDROCK_ERROR_CODES
            ) from error
        except (
            ConnectTimeoutError,
            EndpointConnectionError,
            ReadTimeoutError,
        ) as error:
            raise LLMProviderError(retryable=True) from error
        except BotoCoreError as error:
            raise LLMProviderError(retryable=False) from error
        if metadata is None:
            raise LLMProviderError(retryable=True)
        usage = metadata["usage"]
        metrics = metadata["metrics"]
        return LLMResult(
            text="".join(chunks),
            model_id=self._model_id,
            input_tokens=usage["inputTokens"],
            output_tokens=usage["outputTokens"],
            latency_ms=int(metrics["latencyMs"]),
        )

    def _converse(
        self,
        *,
        messages: list[dict[str, object]],
        tool_config: dict[str, object] | None = None,
        system: list[dict[str, object]] | None = None,
        max_tokens: int = 128,
    ) -> dict[str, Any]:
        request: dict[str, object] = {
            "modelId": self._model_id,
            "messages": messages,
            "inferenceConfig": {"maxTokens": max_tokens, "temperature": 0.0},
        }
        if tool_config is not None:
            request["toolConfig"] = tool_config
        if system is not None:
            request["system"] = system
        try:
            self._reserve_budget()
            return self._get_runtime_client().converse(**request)
        except ClientError as error:
            error_code = error.response.get("Error", {}).get("Code", "")
            raise LLMProviderError(
                retryable=error_code in RETRYABLE_BEDROCK_ERROR_CODES
            ) from error
        except (
            ConnectTimeoutError,
            EndpointConnectionError,
            ReadTimeoutError,
        ) as error:
            raise LLMProviderError(retryable=True) from error
        except BotoCoreError as error:
            raise LLMProviderError(retryable=False) from error

    def _reserve_budget(self) -> None:
        if self._budget is None:
            return
        try:
            self._budget.reserve(self._model_id)
        except BedrockBudgetExceededError as error:
            raise LLMProviderError(
                retryable=False, code="bedrock_budget_exhausted"
            ) from error
        except BedrockBudgetUnavailableError as error:
            raise LLMProviderError(
                retryable=True, code="bedrock_budget_unavailable"
            ) from error

    def _as_llm_result(self, response: Mapping[str, Any]) -> LLMResult:
        return LLMResult(
            text="".join(
                block["text"]
                for block in response["output"]["message"]["content"]
                if "text" in block
            ),
            model_id=response.get("modelId", self._model_id),
            input_tokens=response["usage"]["inputTokens"],
            output_tokens=response["usage"]["outputTokens"],
            latency_ms=response["metrics"]["latencyMs"],
        )

    def _get_runtime_client(self) -> Any:
        if self._runtime_client is None:
            import boto3

            self._runtime_client = boto3.client(
                "bedrock-runtime",
                region_name=self._region_name,
                config=BEDROCK_RUNTIME_CONFIG,
            )
        return self._runtime_client


class ServeLLMClient:
    """OpenAI-compatible client routing through the owned inference gateway (/serve)."""

    _error_prefix = "vllm_gateway"

    def __init__(
        self,
        gateway_url: str,
        model_id: str,
        *,
        timeout_seconds: float = 60.0,
        tenant_id: str = "tenant-default",
        priority: str = "interactive",
        http_client: httpx.Client | None = None,
        answer_thinking_enabled: bool = False,
    ) -> None:
        self._gateway_url = gateway_url.rstrip("/")
        self._model_id = model_id
        self._timeout_seconds = timeout_seconds
        self._tenant_id = tenant_id
        self._priority = priority
        self._http_client = http_client
        # D2: structured/tool-proposal calls always disable thinking; the final answer
        # call defaults to disabled too but is configurable via INFERENCE_ANSWER_THINKING.
        self._answer_thinking_enabled = answer_thinking_enabled

    @property
    def model_id(self) -> str:
        return self._model_id

    def _prepare_request(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> None:
        """Hook for subclasses to adapt the OpenAI-style request in place."""

    def _get_client(self) -> httpx.Client:
        if self._http_client is None:
            self._http_client = httpx.Client(timeout=self._timeout_seconds)
        return self._http_client

    def _build_headers(
        self,
        *,
        partition: PrefixPartition,
        agent_step: int,
        request_id: str | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, str]:
        req_id = request_id or f"req-{uuid.uuid4().hex[:12]}"
        conv_id = conversation_id or f"conv-{uuid.uuid4().hex[:8]}"
        return {
            "content-type": "application/json",
            "x-request-id": req_id,
            "x-conversation-id": conv_id,
            "x-agent-step": str(agent_step),
            "x-tenant-id": self._tenant_id,
            "x-request-priority": self._priority,
            "x-estimated-prompt-tokens": str(partition.estimated_total_tokens),
            "x-deadline-ms": str(int(self._timeout_seconds * 1000)),
            "x-prefix-id": partition.prefix_id,
        }

    def ask(self, prompt: str, *, conversation_id: str | None = None) -> LLMResult:
        partition = build_ask_partition(prompt)
        headers = self._build_headers(
            partition=partition, agent_step=1, conversation_id=conversation_id
        )
        payload: dict[str, Any] = {
            "model": self._model_id,
            "messages": [
                {"role": "system", "content": partition.global_shared},
                {"role": "user", "content": partition.unique_suffix},
            ],
            "max_tokens": 1024,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": self._answer_thinking_enabled},
        }
        return _attribute_errors(
            lambda: self._post_completion(payload, headers), partition.attribution
        )

    def execute_partitioned_call(
        self,
        partition: PrefixPartition,
        *,
        conversation_id: str | None = None,
        agent_step: int = 1,
    ) -> LLMResult:
        headers = self._build_headers(
            partition=partition, agent_step=agent_step, conversation_id=conversation_id
        )
        user_content = (
            f"{partition.conversation_shared}\n\n{partition.unique_suffix}".strip()
        )
        payload: dict[str, Any] = {
            "model": self._model_id,
            "messages": [
                {"role": "system", "content": partition.global_shared},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": 1024,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": self._answer_thinking_enabled},
        }
        return _attribute_errors(
            lambda: self._post_completion(payload, headers), partition.attribution
        )

    def propose_dataset_profile(
        self, prompt: str, *, conversation_id: str | None = None
    ) -> ToolProposalResult:
        return ToolProposalResult(
            name="get_dataset_profile",
            arguments={},
            model_id=self._model_id,
            input_tokens=estimate_tokens(prompt),
            output_tokens=1,
            latency_ms=0,
        )

    def answer_with_dataset_profile(
        self,
        prompt: str,
        dataset_profile: Mapping[str, object],
        *,
        conversation_id: str | None = None,
    ) -> LLMResult:
        partition = build_dataset_profile_partition(prompt, dataset_profile)
        headers = self._build_headers(
            partition=partition, agent_step=2, conversation_id=conversation_id
        )
        user_content = (
            f"{partition.conversation_shared}\n\n{partition.unique_suffix}".strip()
        )
        payload: dict[str, Any] = {
            "model": self._model_id,
            "messages": [
                {"role": "system", "content": partition.global_shared},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": 1024,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": self._answer_thinking_enabled},
        }
        return _attribute_errors(
            lambda: self._post_completion(payload, headers), partition.attribution
        )

    def propose_taxi_query(
        self,
        prompt: str,
        schema: Mapping[str, object],
        *,
        conversation_id: str | None = None,
        repo: Any | None = None,
        current_message_id: str | None = None,
    ) -> ToolProposalResult:
        partition = build_query_proposal_partition(prompt, schema)
        headers = self._build_headers(
            partition=partition, agent_step=1, conversation_id=conversation_id
        )
        proposal_messages = self._build_proposal_messages(
            prompt=prompt,
            partition=partition,
            conversation_id=conversation_id,
            repo=repo,
            current_message_id=current_message_id,
        )
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "query_taxi_data",
                    "description": (
                        "Run one fixed read-only analysis over the pinned NYC Taxi dataset."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "analysis": {
                                "type": "string",
                                "enum": [
                                    "top_pickup_zones",
                                    "trip_volume_by_hour",
                                    "average_distance_by_weekday",
                                ],
                            },
                            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                        },
                        "required": ["analysis", "limit"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "average_trip_metrics",
                    "description": (
                        "Calculate aggregated metrics across boroughs or for one named region."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "region_name": {"type": "string"},
                        },
                    },
                },
            },
        ]
        payload: dict[str, Any] = {
            "model": self._model_id,
            "messages": proposal_messages,
            "tools": tools,
            "tool_choice": "auto",
            "max_tokens": 512,
            "stream": False,
            # D2: structured/tool-proposal calls always disable thinking.
            "chat_template_kwargs": {"enable_thinking": False},
        }
        return _attribute_errors(
            lambda: self._post_tool_proposal(payload, headers), partition.attribution
        )

    def answer_with_query_result(
        self,
        prompt: str,
        query_result: Mapping[str, object],
        *,
        conversation_id: str | None = None,
        repo: Any | None = None,
        current_message_id: str | None = None,
    ) -> LLMResult:
        partition = build_query_answer_partition(prompt, query_result)
        headers = self._build_headers(
            partition=partition, agent_step=2, conversation_id=conversation_id
        )
        payload: dict[str, Any] = {
            "model": self._model_id,
            "messages": self._build_answer_messages(
                partition=partition,
                conversation_id=conversation_id,
                repo=repo,
                current_message_id=current_message_id,
            ),
            "max_tokens": 1024,
            "stream": False,
            "chat_template_kwargs": {"enable_thinking": self._answer_thinking_enabled},
        }
        return _attribute_errors(
            lambda: self._post_completion(payload, headers), partition.attribution
        )

    def stream_answer_with_query_result(
        self,
        prompt: str,
        query_result: Mapping[str, object],
        delta_callback: Callable[[str], None],
        *,
        conversation_id: str | None = None,
        repo: Any | None = None,
        current_message_id: str | None = None,
    ) -> LLMResult:
        partition = build_query_answer_partition(prompt, query_result)
        headers = self._build_headers(
            partition=partition, agent_step=2, conversation_id=conversation_id
        )
        payload: dict[str, Any] = {
            "model": self._model_id,
            "messages": self._build_answer_messages(
                partition=partition,
                conversation_id=conversation_id,
                repo=repo,
                current_message_id=current_message_id,
            ),
            "max_tokens": 1024,
            "stream": True,
            "chat_template_kwargs": {"enable_thinking": self._answer_thinking_enabled},
        }
        return _attribute_errors(
            lambda: self._stream_completion(payload, headers, delta_callback),
            partition.attribution,
        )

    def _build_proposal_messages(
        self,
        *,
        prompt: str,
        partition: PrefixPartition,
        conversation_id: str | None,
        repo: Any | None,
        current_message_id: str | None = None,
    ) -> list[dict[str, str]]:
        """Build the messages payload for tool proposal. Uses the real stored
        conversation history (prefix contract v2) when a conversation_id and repo
        are both available; otherwise falls back to today's single-turn payload
        so call paths without durable state keep working unchanged."""
        if conversation_id is not None and repo is not None:
            rendered = render_conversation_prompt(
                conversation_id,
                partition.unique_suffix,
                repo,
                system_prompt=partition.global_shared,
                current_message_id=current_message_id,
            )
            return rendered.messages
        return [
            {"role": "system", "content": partition.global_shared},
            {"role": "user", "content": partition.unique_suffix},
        ]

    def _build_answer_messages(
        self,
        *,
        partition: PrefixPartition,
        conversation_id: str | None,
        repo: Any | None,
        current_message_id: str | None = None,
    ) -> list[dict[str, str]]:
        """Build the messages payload for the final answer. Uses real stored
        conversation history (prefix contract v2) when available; otherwise falls
        back to today's single-turn payload."""
        if conversation_id is not None and repo is not None:
            rendered = render_conversation_prompt(
                conversation_id,
                partition.unique_suffix,
                repo,
                system_prompt=partition.global_shared,
                current_message_id=current_message_id,
            )
            return rendered.messages
        user_content = (
            f"{partition.conversation_shared}\n\n{partition.unique_suffix}".strip()
        )
        return [
            {"role": "system", "content": partition.global_shared},
            {"role": "user", "content": user_content},
        ]

    def _post_completion(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> LLMResult:
        self._prepare_request(payload, headers)
        client = self._get_client()
        start = time.monotonic()
        try:
            response = client.post(self._gateway_url, json=payload, headers=headers)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise LLMProviderError(
                retryable=True, code=f"{self._error_prefix}_unavailable"
            ) from exc
        except httpx.RequestError as exc:
            raise LLMProviderError(
                retryable=False, code=f"{self._error_prefix}_request_error"
            ) from exc

        if response.status_code != 200:
            retryable = response.status_code in (503, 504, 529)
            raise LLMProviderError(
                retryable=retryable,
                code=f"{self._error_prefix}_http_{response.status_code}",
            )

        latency_ms = int((time.monotonic() - start) * 1000)
        data = response.json()
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        raw_text = message.get("content") or choice.get("text", "")
        finish_reason = choice.get("finish_reason")
        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        usage_known = _usage_known(usage)

        separate_reasoning = message.get("reasoning_content")
        if isinstance(separate_reasoning, str) and separate_reasoning:
            visible_text = raw_text
        else:
            _, visible_text = split_thinking(raw_text)

        reasoning_tokens, reasoning_unavailable = self._extract_reasoning_tokens(usage)
        visible_tokens = None
        visible_unavailable = None
        if reasoning_tokens is not None:
            visible_tokens = max(0, output_tokens - reasoning_tokens)
        else:
            visible_unavailable = "provider_did_not_report_token_split"

        return LLMResult(
            text=visible_text,
            model_id=data.get("model", self._model_id),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
            configured_max_tokens=payload.get("max_tokens"),
            reasoning_tokens=reasoning_tokens,
            reasoning_tokens_unavailable_reason=reasoning_unavailable,
            visible_answer_tokens=visible_tokens,
            visible_answer_tokens_unavailable_reason=visible_unavailable,
            usage_known=usage_known,
            **self._cost_fields(
                response.headers, payload, input_tokens, output_tokens, usage_known
            ),
        )

    def _cost_fields(
        self,
        response_headers: Mapping[str, str],
        payload: Mapping[str, Any],
        input_tokens: int,
        output_tokens: int,
        usage_known: bool,
    ) -> dict[str, Any]:
        """Role/alias/cost metadata for the result; the self-hosted path adds none."""
        return {}

    @staticmethod
    def _extract_reasoning_tokens(
        usage: Mapping[str, Any],
    ) -> tuple[int | None, str | None]:
        """Best-effort extraction of a provider-reported reasoning-token count.
        Never estimates from word counts; returns (None, reason) when unavailable."""
        details = usage.get("completion_tokens_details")
        if isinstance(details, Mapping):
            value = details.get("reasoning_tokens")
            if isinstance(value, int) and not isinstance(value, bool):
                return value, None
        return None, "provider_did_not_report_token_split"

    def _post_tool_proposal(
        self,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> ToolProposalResult:
        """Exactly one HTTP request. A non-200 response, a missing tool call, or a
        malformed tool call each become a typed, non-retryable failure -- never a
        silent retry or a keyword-guessed tool."""
        self._prepare_request(payload, headers)
        client = self._get_client()
        start = time.monotonic()
        try:
            response = client.post(self._gateway_url, json=payload, headers=headers)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise LLMProviderError(
                retryable=True, code=f"{self._error_prefix}_unavailable"
            ) from exc
        except httpx.RequestError as exc:
            raise LLMProviderError(
                retryable=False, code=f"{self._error_prefix}_request_error"
            ) from exc

        if response.status_code != 200:
            if (
                payload.get("tools")
                and 400 <= response.status_code < 500
                and MALFORMED_TOOL_USE_MARKER
                in response.text[:_PROVIDER_BODY_PEEK_CHARS]
            ):
                raise LLMProviderError(
                    retryable=False,
                    code="malformed_tool_use",
                    model_id=payload.get("model") or self._model_id,
                    provider_status=response.status_code,
                    provider_call_id=response.headers.get("x-litellm-call-id"),
                    attempts=1,
                )
            retryable = response.status_code in (503, 504, 529)
            raise LLMProviderError(
                retryable=retryable,
                code=f"{self._error_prefix}_http_{response.status_code}",
            )

        latency_ms = int((time.monotonic() - start) * 1000)
        data = response.json()
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason")
        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        usage_known = _usage_known(usage)

        served_model_id = data.get("model", self._model_id)

        tool_calls = message.get("tool_calls", [])
        if not tool_calls or not isinstance(tool_calls, list):
            content_text = (message.get("content", "") or "").strip()
            user_msg = payload.get("messages", [{}])[-1].get("content", "")
            outcome = classify_non_tool_outcome(content_text, str(user_msg))
            reasoning = _reasoning_facts(message, usage)
            if outcome == "no_tool" and not content_text and reasoning["present"]:
                # Blank content but the model reasoned. That is NOT necessarily a
                # decline (data may exist), so it is a distinct no_visible_output
                # outcome. finish_reason "length" means generation was exhausted.
                return ToolProposalResult(
                    name="",
                    arguments=None,
                    outcome="no_visible_output",
                    decline_reason=None,
                    reasoning_tokens=reasoning["tokens"],
                    reasoning_present=True,
                    reasoning_chars=reasoning["chars"],
                    output_basis=(
                        "reasoning_length"
                        if finish_reason == "length"
                        else "reasoning_only"
                    ),
                    model_id=served_model_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency_ms,
                    finish_reason=finish_reason,
                    configured_max_tokens=payload.get("max_tokens"),
                    usage_known=usage_known,
                    **self._cost_fields(
                        response.headers,
                        payload,
                        input_tokens,
                        output_tokens,
                        usage_known,
                    ),
                )
            if outcome == "no_tool":
                raise LLMProviderError(
                    retryable=False,
                    code="no_tool_call",
                    model_id=served_model_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency_ms,
                    finish_reason=finish_reason,
                )
            return ToolProposalResult(
                name="",
                arguments=None,
                outcome=outcome,
                decline_reason=content_text,
                output_basis="content",
                model_id=served_model_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                finish_reason=finish_reason,
                configured_max_tokens=payload.get("max_tokens"),
                usage_known=usage_known,
                **self._cost_fields(
                    response.headers, payload, input_tokens, output_tokens, usage_known
                ),
            )

        if len(tool_calls) > 1:
            raise LLMProviderError(
                retryable=False,
                code="multiple_tool_calls",
                model_id=served_model_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                finish_reason=finish_reason,
            )
        call = tool_calls[0]
        func = call.get("function", {})
        name = func.get("name", "")
        raw_args = func.get("arguments", {})
        if isinstance(raw_args, str):
            try:
                arguments: object = json.loads(raw_args)
            except json.JSONDecodeError as exc:
                raise LLMProviderError(
                    retryable=False,
                    code="invalid_tool_call",
                    model_id=served_model_id,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=latency_ms,
                    finish_reason=finish_reason,
                ) from exc
        else:
            arguments = raw_args

        if not isinstance(name, str) or not name:
            raise LLMProviderError(
                retryable=False,
                code="invalid_tool_call",
                model_id=served_model_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                finish_reason=finish_reason,
            )

        return ToolProposalResult(
            name=name,
            arguments=arguments,
            outcome="tool_call",
            model_id=served_model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
            configured_max_tokens=payload.get("max_tokens"),
            usage_known=usage_known,
            **self._cost_fields(
                response.headers, payload, input_tokens, output_tokens, usage_known
            ),
        )

    def _stream_completion(
        self,
        payload: dict[str, Any],
        headers: dict[str, str],
        delta_callback: Callable[[str], None],
    ) -> LLMResult:
        self._prepare_request(payload, headers)
        client = self._get_client()
        start = time.monotonic()
        accumulated: list[str] = []
        model_id = self._model_id
        input_tokens = 0
        output_tokens = 0
        usage_seen = False
        response_headers: Mapping[str, str] = {}
        finish_reason: str | None = None
        reasoning_tokens: int | None = None
        splitter = ThinkingSplitter()
        ttft_ms: int | None = None
        ttft_unavailable_reason: str | None = None
        first_visible_answer_ms: int | None = None

        def note_ttft() -> None:
            nonlocal ttft_ms
            if ttft_ms is None:
                ttft_ms = int((time.monotonic() - start) * 1000)

        def emit_visible(text: str) -> None:
            nonlocal first_visible_answer_ms
            if not text:
                return
            if first_visible_answer_ms is None:
                first_visible_answer_ms = int((time.monotonic() - start) * 1000)
            accumulated.append(text)
            delta_callback(text)

        try:
            with client.stream(
                "POST", self._gateway_url, json=payload, headers=headers
            ) as response:
                if response.status_code != 200:
                    retryable = response.status_code in (503, 504, 529)
                    raise LLMProviderError(
                        retryable=retryable,
                        code=f"{self._error_prefix}_http_{response.status_code}",
                    )
                response_headers = response.headers
                content_type = response.headers.get("content-type", "")
                if "application/json" in content_type:
                    content_bytes = response.read()
                    try:
                        data = json.loads(content_bytes)
                        choice = data.get("choices", [{}])[0]
                        message = choice.get("message", {})
                        finish_reason = choice.get("finish_reason")
                        raw_text = message.get("content") or choice.get("text", "")
                        separate_reasoning = message.get("reasoning_content")
                        # The full response body has already been read at this
                        # point (this is a JSON-shaped, not SSE-shaped, gateway
                        # response), so there is no genuine time-to-first-token
                        # to report. Leave ttft_ms unset with an explicit
                        # reason rather than mislabeling full latency as TTFT.
                        ttft_unavailable_reason = "non_streaming_json_response"
                        usage = data.get("usage", {})
                        usage_seen = usage_seen or _usage_known(usage)
                        input_tokens = usage.get("prompt_tokens", input_tokens)
                        output_tokens = usage.get("completion_tokens", output_tokens)
                        rt, _ = self._extract_reasoning_tokens(usage)
                        reasoning_tokens = rt
                        if isinstance(separate_reasoning, str) and separate_reasoning:
                            emit_visible(raw_text)
                        elif raw_text:
                            emit_visible(splitter.feed(raw_text))
                        trailing_visible = splitter.flush()
                        emit_visible(trailing_visible)
                    except json.JSONDecodeError:
                        pass
                else:
                    for line in response.iter_lines():
                        if not line:
                            continue
                        if line.startswith("data: "):
                            line_data = line[6:].strip()
                            if line_data == "[DONE]":
                                break
                            try:
                                chunk = json.loads(line_data)
                            except json.JSONDecodeError:
                                continue
                            model_id = chunk.get("model", model_id)
                            choices = chunk.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {})
                                choice_finish = choices[0].get("finish_reason")
                                if choice_finish:
                                    finish_reason = choice_finish
                                reasoning_delta = delta.get("reasoning_content")
                                content = delta.get("content", "")
                                if reasoning_delta or content:
                                    note_ttft()
                                if isinstance(reasoning_delta, str) and reasoning_delta:
                                    splitter.reasoning_text += reasoning_delta
                                if content:
                                    emit_visible(splitter.feed(content))
                            if "usage" in chunk and chunk["usage"]:
                                usage_dict = chunk["usage"]
                                usage_seen = usage_seen or _usage_known(usage_dict)
                                input_tokens = usage_dict.get(
                                    "prompt_tokens", input_tokens
                                )
                                output_tokens = usage_dict.get(
                                    "completion_tokens", output_tokens
                                )
                                rt, _ = self._extract_reasoning_tokens(usage_dict)
                                if rt is not None:
                                    reasoning_tokens = rt
                    trailing_visible = splitter.flush()
                    emit_visible(trailing_visible)

        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise LLMProviderError(
                retryable=True, code=f"{self._error_prefix}_unavailable"
            ) from exc
        except httpx.RequestError as exc:
            raise LLMProviderError(
                retryable=False, code=f"{self._error_prefix}_request_error"
            ) from exc

        latency_ms = int((time.monotonic() - start) * 1000)
        full_text = "".join(accumulated)
        if output_tokens == 0:
            output_tokens = max(1, len(full_text.split()))

        visible_tokens = None
        visible_unavailable = None
        if reasoning_tokens is not None:
            visible_tokens = max(0, output_tokens - reasoning_tokens)
        else:
            visible_unavailable = "provider_did_not_report_token_split"

        return LLMResult(
            text=full_text,
            model_id=model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
            configured_max_tokens=payload.get("max_tokens"),
            ttft_ms=ttft_ms,
            ttft_unavailable_reason=ttft_unavailable_reason,
            first_visible_answer_ms=first_visible_answer_ms,
            reasoning_tokens=reasoning_tokens,
            reasoning_tokens_unavailable_reason=(
                None
                if reasoning_tokens is not None
                else "provider_did_not_report_token_split"
            ),
            visible_answer_tokens=visible_tokens,
            visible_answer_tokens_unavailable_reason=visible_unavailable,
            usage_known=usage_seen,
            **self._cost_fields(
                response_headers, payload, input_tokens, output_tokens, usage_seen
            ),
        )


class LiteLLMClient(ServeLLMClient):
    """OpenAI-compatible client for the opt-in LiteLLM Proxy (Bedrock-first).

    The agent picks stable aliases; the proxy owns the provider mapping. Tool
    proposals use the planner alias and answers use the answer alias."""

    _error_prefix = "litellm_proxy"

    def __init__(
        self,
        proxy_url: str,
        *,
        planner_alias: str = "taxi-planner",
        answer_alias: str = "taxi-answer",
        api_key: str | None = None,
        budget: Any | None = None,
        budget_model_id: str = DEFAULT_MODEL_ID,
        timeout_seconds: float = 60.0,
        http_client: httpx.Client | None = None,
        runtime: ModelPolicyRuntime | None = None,
    ) -> None:
        super().__init__(
            f"{proxy_url.rstrip('/')}/v1/chat/completions",
            answer_alias,
            timeout_seconds=timeout_seconds,
            http_client=http_client,
        )
        self._planner_alias = planner_alias
        self._api_key = api_key
        # The proxy fronts billed Bedrock, so the shared DynamoDB allowance still applies.
        self._budget = budget
        self._budget_model_id = budget_model_id
        # Role->alias, entitlements, caps, run budgets, pricing (#170). Defaults keep
        # the plain planner/answer alias behaviour with no tenant or run limits.
        self._runtime = runtime or ModelPolicyRuntime.default(
            planner_alias, answer_alias
        )

    @property
    def pricing(self) -> PricingTable:
        """The model-policy price table (alias -> rate), for cost estimation by callers."""
        return self._runtime.pricing

    @staticmethod
    def _role(payload: Mapping[str, Any]) -> str:
        return "planner" if payload.get("tools") else "answer"

    def _governed(
        self, payload: dict[str, Any], call: Callable[[dict[str, Any]], Any]
    ) -> Any:
        """Apply model policy around one logical model call.

        Order: entitlement -> per-call cap -> per-run reservation -> proxy call (which
        still hits the authoritative DynamoDB monthly guard) -> commit/release."""
        rt = self._runtime
        ctx = current_call_context()
        role = self._role(payload)
        try:
            rp = rt.policy.resolve(role, ctx.tenant_id)
        except PolicyDeniedError as error:
            raise LLMProviderError(
                retryable=False, code="model_not_entitled"
            ) from error
        fallbacks, rejected = rt.policy.allowed_fallbacks(rp, ctx.tenant_id)
        if rejected:
            logger.warning("fallback_rejected role=%s aliases=%s", role, rejected)

        max_tokens = payload.get("max_tokens") or rp.max_tokens
        if rp.max_tokens is not None and max_tokens is not None:
            max_tokens = min(max_tokens, rp.max_tokens)
        reserve_out = max_tokens or 1024
        reserve_in = estimate_tokens(json.dumps(payload.get("messages", [])))

        candidates = [rp.alias, *fallbacks]
        for index, alias in enumerate(candidates):
            attempt = dict(payload)
            attempt["model"] = alias
            if max_tokens is not None:
                attempt["max_tokens"] = max_tokens
            reservation = None
            if ctx.run_id is not None:
                try:
                    reservation = rt.budgets.reserve(
                        ctx.run_id,
                        reserve_in + reserve_out,
                        rt.pricing.estimate(alias, reserve_in, reserve_out),
                    )
                except RunBudgetExceededError as error:
                    raise LLMProviderError(
                        retryable=False, code="run_budget_exhausted"
                    ) from error
            try:
                attempt_token = _PROXY_ATTEMPT.set(index + 1)
                try:
                    result = call(attempt)
                finally:
                    _PROXY_ATTEMPT.reset(attempt_token)
            except LLMProviderError as error:
                if reservation:
                    rt.budgets.release(reservation)
                last = index == len(candidates) - 1
                # Never fall back past the monthly guard, and only on transient errors.
                if (
                    last
                    or not error.retryable
                    or error.code.startswith("bedrock_budget")
                ):
                    raise
                continue
            except BaseException:
                if reservation:
                    rt.budgets.release(reservation)
                raise
            if reservation:
                known = result.usage_known
                rt.budgets.commit(
                    reservation,
                    input_tokens=result.input_tokens if known else None,
                    output_tokens=result.output_tokens if known else None,
                    usd=result.cost_usd,
                )
            return replace(result, fallback_used=index > 0)
        raise AssertionError("unreachable")  # pragma: no cover

    def _post_completion(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> LLMResult:
        return self._governed(
            payload, lambda p: ServeLLMClient._post_completion(self, p, headers)
        )

    def _post_tool_proposal(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> ToolProposalResult:
        return self._governed(
            payload, lambda p: ServeLLMClient._post_tool_proposal(self, p, headers)
        )

    def _stream_completion(
        self,
        payload: dict[str, Any],
        headers: dict[str, str],
        delta_callback: Callable[[str], None],
    ) -> LLMResult:
        return self._governed(
            payload,
            lambda p: ServeLLMClient._stream_completion(
                self, p, headers, delta_callback
            ),
        )

    def _cost_fields(
        self,
        response_headers: Mapping[str, str],
        payload: Mapping[str, Any],
        input_tokens: int,
        output_tokens: int,
        usage_known: bool,
    ) -> dict[str, Any]:
        alias = payload.get("model", "")
        pricing = self._runtime.pricing
        fields: dict[str, Any] = {
            "role": self._role(payload),
            "model_alias": alias,
            "pricing_version": pricing.version,
            "cost_usd": None,
            "cost_kind": "unknown",
        }
        # The proxy's own computed cost is the billed figure when it reports one.
        try:
            billed = float(response_headers.get("x-litellm-response-cost", ""))
        except (TypeError, ValueError):
            billed = None
        if billed is not None:
            fields.update(cost_usd=billed, cost_kind="billed")
        elif usage_known:
            estimate = pricing.estimate(alias, input_tokens, output_tokens)
            if estimate is not None:
                fields.update(cost_usd=estimate, cost_kind="estimated")
        return fields

    def _reserve_budget(self) -> None:
        if self._budget is None:
            return
        try:
            self._budget.reserve(self._budget_model_id)
        except BedrockBudgetExceededError as error:
            raise LLMProviderError(
                retryable=False, code="bedrock_budget_exhausted"
            ) from error
        except BedrockBudgetUnavailableError as error:
            raise LLMProviderError(
                retryable=True, code="bedrock_budget_unavailable"
            ) from error

    def propose_taxi_query(
        self,
        prompt: str,
        schema: Mapping[str, object],
        *,
        conversation_id: str | None = None,
        repo: Any | None = None,
        current_message_id: str | None = None,
    ) -> ToolProposalResult:
        # Same prompt and tool schemas as the direct Bedrock client, so the proxy
        # path is behaviourally equivalent (no prompt/strategy change).
        partition = build_query_proposal_partition(prompt, schema)
        headers = self._build_headers(
            partition=partition, agent_step=1, conversation_id=conversation_id
        )
        message, attribution = _taxi_proposal(prompt, schema)
        payload: dict[str, Any] = {
            "model": self._planner_alias,
            "messages": [{"role": "user", "content": message}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": spec["name"],
                        "description": spec["description"],
                        "parameters": spec["inputSchema"],
                    },
                }
                for spec in TAXI_PROPOSAL_TOOL_SPECS
            ],
            "max_tokens": 512,
            "stream": False,
        }
        return _attribute_errors(
            lambda: self._post_tool_proposal(payload, headers), attribution
        )

    @staticmethod
    def _add_correlation_headers(headers: dict[str, str]) -> None:
        """Map proxy spend rows to app ids (#189); fail-open, never breaks a call.

        `x-litellm-spend-logs-metadata` is sent whenever a run or call id exists (the
        1:1 map to capture call_id, not eval-only); `x-litellm-tags` only for eval runs.
        `eval_run_id` is a correlation label, not an authorization or billing boundary.
        """
        try:
            ctx = current_call_context()
            call_id = current_llm_call_id()
            if ctx.run_id is None and call_id is None and ctx.eval_run_id is None:
                return
            meta: dict[str, Any] = {}
            if ctx.run_id is not None:
                meta["run_id"] = ctx.run_id
            if call_id is not None:
                meta["call_id"] = call_id
            meta["attempt"] = _PROXY_ATTEMPT.get()
            if ctx.eval_run_id:
                meta["eval_run_id"] = ctx.eval_run_id
            headers["x-litellm-spend-logs-metadata"] = json.dumps(
                meta, separators=(",", ":"), ensure_ascii=True
            )
            if ctx.eval_run_id:
                headers["x-litellm-tags"] = f"eval_run_id:{ctx.eval_run_id}"
        except Exception:
            logger.warning("litellm_correlation_headers_failed", exc_info=True)

    def _prepare_request(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> None:
        self._reserve_budget()
        # vLLM-only knob; Bedrock via the proxy must not receive it.
        payload.pop("chat_template_kwargs", None)
        # Match the direct Bedrock client: deterministic sampling, and tool use is
        # forced for proposals (LiteLLM maps "required" to Bedrock toolChoice any).
        payload["temperature"] = 0.0
        if payload.get("tools"):
            payload["tool_choice"] = "auto"
        if payload.get("stream"):
            payload["stream_options"] = {"include_usage": True}
        # Group a conversation's planner/answer calls in the proxy's Logs view.
        if conversation_id := headers.get("x-conversation-id"):
            headers["x-litellm-session-id"] = conversation_id
        self._add_correlation_headers(headers)
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"
        # W3C trace context so proxy spans join the app trace.
        propagate.inject(headers)


def create_llm_client(
    settings: Settings,
    *,
    budget: Any | None = None,
    runtime_client: Any | None = None,
    http_client: Any | None = None,
) -> LLMClient:
    if settings.llm_provider == "fake":
        return LocalFakeLLMClient()
    if settings.llm_provider in ("vllm", "serve"):
        settings.validate_inference_alignment()
        return ServeLLMClient(
            gateway_url=settings.inference_gateway_url,
            model_id=settings.inference_model_id,
            http_client=http_client,
            answer_thinking_enabled=settings.inference_answer_thinking,
        )
    if settings.llm_provider == "litellm":
        settings.validate_litellm_alignment()
    else:
        settings.validate_m4_alignment()
    if budget is None:
        if not settings.dynamodb_table_name:
            raise ValueError(
                f"LLM_PROVIDER={settings.llm_provider} requires DYNAMODB_TABLE_NAME "
                "for the shared Bedrock allowance"
            )
        budget = DynamoDBBedrockBudget(
            settings.dynamodb_table_name,
            monthly_limit_micro_usd=monthly_limit_micro_usd(
                settings.global_bedrock_monthly_limit_usd
            ),
            region_name=settings.aws_region,
        )
    if settings.llm_provider == "litellm":
        return LiteLLMClient(
            settings.litellm_proxy_url,
            planner_alias=settings.litellm_planner_alias,
            answer_alias=settings.litellm_answer_alias,
            api_key=settings.litellm_api_key,
            budget=budget,
            budget_model_id=settings.llm_model_id,
            http_client=http_client,
            runtime=ModelPolicyRuntime.default(
                settings.litellm_planner_alias,
                settings.litellm_answer_alias,
                policy_file=settings.model_policy_file,
                run_max_tokens=settings.run_max_tokens,
                run_max_usd=settings.run_max_usd,
            ),
        )
    return BedrockLLMClient(
        settings.llm_model_id,
        settings.aws_region,
        runtime_client=runtime_client,
        budget=budget,
    )
