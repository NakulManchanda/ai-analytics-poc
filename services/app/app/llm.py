from collections.abc import Mapping
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
        import json

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
