import json
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
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

from app.bedrock_budget import (
    BedrockBudgetExceededError,
    BedrockBudgetUnavailableError,
    DynamoDBBedrockBudget,
    monthly_limit_micro_usd,
)
from app.config import DEFAULT_MODEL_ID, Settings
from app.prefix import (
    PrefixPartition,
    build_ask_partition,
    build_dataset_profile_partition,
    build_query_answer_partition,
    build_query_proposal_partition,
    estimate_tokens,
)

RETRYABLE_BEDROCK_ERROR_CODES = {
    "InternalServerException",
    "ModelNotReadyException",
    "ModelTimeoutException",
    "ServiceUnavailableException",
    "ThrottlingException",
}
BEDROCK_RUNTIME_CONFIG = Config(retries={"total_max_attempts": 1})


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
        response = self._converse(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "text": (
                                "Answer the user's question using only this governed dataset "
                                f"profile. Question: {prompt}\nDataset profile: {profile_json}"
                            )
                        }
                    ],
                }
            ],
        )
        return self._as_llm_result(response)

    def propose_taxi_query(
        self, prompt: str, schema: Mapping[str, object]
    ) -> ToolProposalResult:
        schema_json = json.dumps(schema, separators=(",", ":"), allow_nan=False)
        response = self._converse(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "text": (
                                "Choose exactly ONE governed analysis that answers the question. "
                                "Do not make multiple tool calls. To compare all boroughs "
                                "across NYC, call average_trip_metrics with region_name omitted. "
                                f"Dataset schema: {schema_json}\nQuestion: {prompt}"
                            )
                        }
                    ],
                }
            ],
            tool_config={
                "tools": [
                    {
                        "toolSpec": {
                            "name": "query_taxi_data",
                            "description": (
                                "Run one fixed read-only analysis over the pinned NYC Taxi dataset."
                            ),
                            "inputSchema": {
                                "json": {
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
                                        "limit": {
                                            "type": "integer",
                                            "minimum": 1,
                                            "maximum": 20,
                                        },
                                    },
                                    "required": ["analysis", "limit"],
                                    "additionalProperties": False,
                                }
                            },
                        }
                    },
                    {
                        "toolSpec": {
                            "name": "average_trip_metrics",
                            "description": (
                                "Compare average trip distance and fare amount across governed "
                                "pickup boroughs (Manhattan, Brooklyn, Queens, Bronx, "
                                "Staten Island). Leave region_name empty to compare all "
                                "boroughs, or provide exactly one single borough name."
                            ),
                            "inputSchema": {
                                "json": {
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
                                }
                            },
                        }
                    },
                ],
                "toolChoice": {"any": {}},
            },
        )
        content = response["output"]["message"]["content"]
        tool_uses = [block["toolUse"] for block in content if "toolUse" in block]
        if len(tool_uses) == 1:
            name = tool_uses[0].get("name", "")
            arguments = tool_uses[0].get("input")
        elif len(tool_uses) > 1 and all(
            u.get("name") == "average_trip_metrics" for u in tool_uses
        ):
            name = "average_trip_metrics"
            arguments = {}
        else:
            name: object = ""
            arguments: object = None
        return ToolProposalResult(
            name=name if isinstance(name, str) else "",
            arguments=arguments,
            model_id=response.get("modelId", self._model_id),
            input_tokens=response["usage"]["inputTokens"],
            output_tokens=response["usage"]["outputTokens"],
            latency_ms=response["metrics"]["latencyMs"],
        )

    def answer_with_query_result(
        self, prompt: str, query_result: Mapping[str, object]
    ) -> LLMResult:
        result_json = json.dumps(query_result, separators=(",", ":"), allow_nan=False)
        response = self._converse(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "text": (
                                "Answer the user's question using only this governed query "
                                f"result. Question: {prompt}\nQuery result: {result_json}"
                            )
                        }
                    ],
                }
            ],
        )
        return self._as_llm_result(response)

    def stream_answer_with_query_result(
        self,
        prompt: str,
        query_result: Mapping[str, object],
        on_delta: Callable[[str], None],
    ) -> LLMResult:
        result_json = json.dumps(query_result, separators=(",", ":"), allow_nan=False)
        request = {
            "modelId": self._model_id,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "text": (
                                "Answer the user's question using only this governed query "
                                f"result. Question: {prompt}\nQuery result: {result_json}"
                            )
                        }
                    ],
                }
            ],
            "inferenceConfig": {"maxTokens": 128, "temperature": 0.0},
        }
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
    ) -> dict[str, Any]:
        request: dict[str, object] = {
            "modelId": self._model_id,
            "messages": messages,
            "inferenceConfig": {"maxTokens": 128, "temperature": 0.0},
        }
        if tool_config is not None:
            request["toolConfig"] = tool_config
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
        return self._post_completion(payload, headers)

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
        return self._post_completion(payload, headers)

    def propose_taxi_query(
        self,
        prompt: str,
        schema: Mapping[str, object],
        *,
        conversation_id: str | None = None,
    ) -> ToolProposalResult:
        partition = build_query_proposal_partition(prompt, schema)
        headers = self._build_headers(
            partition=partition, agent_step=1, conversation_id=conversation_id
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
            "messages": [
                {"role": "system", "content": partition.global_shared},
                {"role": "user", "content": partition.unique_suffix},
            ],
            "tools": tools,
            "tool_choice": "auto",
            "max_tokens": 512,
            "stream": False,
            # D2: structured/tool-proposal calls always disable thinking.
            "chat_template_kwargs": {"enable_thinking": False},
        }
        return self._post_tool_proposal(payload, headers)

    def answer_with_query_result(
        self,
        prompt: str,
        query_result: Mapping[str, object],
        *,
        conversation_id: str | None = None,
    ) -> LLMResult:
        partition = build_query_answer_partition(prompt, query_result)
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
        return self._post_completion(payload, headers)

    def stream_answer_with_query_result(
        self,
        prompt: str,
        query_result: Mapping[str, object],
        delta_callback: Callable[[str], None],
        *,
        conversation_id: str | None = None,
    ) -> LLMResult:
        partition = build_query_answer_partition(prompt, query_result)
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
            "stream": True,
            "chat_template_kwargs": {"enable_thinking": self._answer_thinking_enabled},
        }
        return self._stream_completion(payload, headers, delta_callback)

    def _post_completion(
        self, payload: dict[str, Any], headers: dict[str, str]
    ) -> LLMResult:
        client = self._get_client()
        start = time.monotonic()
        try:
            response = client.post(self._gateway_url, json=payload, headers=headers)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise LLMProviderError(
                retryable=True, code="vllm_gateway_unavailable"
            ) from exc
        except httpx.RequestError as exc:
            raise LLMProviderError(
                retryable=False, code="vllm_gateway_request_error"
            ) from exc

        if response.status_code != 200:
            retryable = response.status_code in (503, 504, 529)
            raise LLMProviderError(
                retryable=retryable,
                code=f"vllm_gateway_http_{response.status_code}",
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
        )

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
        client = self._get_client()
        start = time.monotonic()
        try:
            response = client.post(self._gateway_url, json=payload, headers=headers)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise LLMProviderError(
                retryable=True, code="vllm_gateway_unavailable"
            ) from exc
        except httpx.RequestError as exc:
            raise LLMProviderError(
                retryable=False, code="vllm_gateway_request_error"
            ) from exc

        if response.status_code != 200:
            retryable = response.status_code in (503, 504, 529)
            raise LLMProviderError(
                retryable=retryable,
                code=f"vllm_gateway_http_{response.status_code}",
            )

        latency_ms = int((time.monotonic() - start) * 1000)
        data = response.json()
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason")
        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)

        served_model_id = data.get("model", self._model_id)

        tool_calls = message.get("tool_calls", [])
        if not tool_calls or not isinstance(tool_calls, list):
            raise LLMProviderError(
                retryable=False,
                code="no_tool_call",
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
            model_id=served_model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
            configured_max_tokens=payload.get("max_tokens"),
        )

    def _stream_completion(
        self,
        payload: dict[str, Any],
        headers: dict[str, str],
        delta_callback: Callable[[str], None],
    ) -> LLMResult:
        client = self._get_client()
        start = time.monotonic()
        accumulated: list[str] = []
        model_id = self._model_id
        input_tokens = 0
        output_tokens = 0
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
                        code=f"vllm_gateway_http_{response.status_code}",
                    )
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
                retryable=True, code="vllm_gateway_unavailable"
            ) from exc
        except httpx.RequestError as exc:
            raise LLMProviderError(
                retryable=False, code="vllm_gateway_request_error"
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
        )


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
    settings.validate_m4_alignment()
    if budget is None:
        if not settings.dynamodb_table_name:
            raise ValueError(
                "LLM_PROVIDER=bedrock requires DYNAMODB_TABLE_NAME for the shared Bedrock allowance"
            )
        budget = DynamoDBBedrockBudget(
            settings.dynamodb_table_name,
            monthly_limit_micro_usd=monthly_limit_micro_usd(
                settings.global_bedrock_monthly_limit_usd
            ),
            region_name=settings.aws_region,
        )
    return BedrockLLMClient(
        settings.llm_model_id,
        settings.aws_region,
        runtime_client=runtime_client,
        budget=budget,
    )
