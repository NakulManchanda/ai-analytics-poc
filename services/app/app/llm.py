import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from app.config import DEFAULT_MODEL_ID, Settings

RETRYABLE_BEDROCK_ERROR_CODES = {
    "InternalServerException",
    "ModelNotReadyException",
    "ModelTimeoutException",
    "ServiceUnavailableException",
    "ThrottlingException",
}
BEDROCK_RUNTIME_CONFIG = Config(retries={"total_max_attempts": 1})


class LLMProviderError(Exception):
    def __init__(self, retryable: bool) -> None:
        super().__init__()
        self.retryable = retryable


@dataclass(frozen=True)
class LLMResult:
    text: str
    model_id: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


@dataclass(frozen=True)
class ToolProposalResult:
    name: str
    arguments: object
    model_id: str
    input_tokens: int
    output_tokens: int
    latency_ms: int


class LLMClient(Protocol):
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
    ) -> None:
        self._model_id = model_id
        self._region_name = region_name
        self._runtime_client = runtime_client

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


def create_llm_client(settings: Settings) -> LLMClient:
    if settings.llm_provider == "fake":
        return LocalFakeLLMClient()
    settings.validate_m4_alignment()
    return BedrockLLMClient(settings.llm_model_id, settings.aws_region)
