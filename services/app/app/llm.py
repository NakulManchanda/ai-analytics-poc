from dataclasses import dataclass
from typing import Any, Protocol

from botocore.exceptions import BotoCoreError, ClientError, EndpointConnectionError

from app.config import Settings

RETRYABLE_BEDROCK_ERROR_CODES = {
    "InternalServerException",
    "ModelNotReadyException",
    "ModelTimeoutException",
    "ServiceUnavailableException",
    "ThrottlingException",
}


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


class LLMClient(Protocol):
    def ask(self, prompt: str) -> LLMResult: ...


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
        try:
            response = self._get_runtime_client().converse(
                modelId=self._model_id,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": 128, "temperature": 0.0},
            )
        except ClientError as error:
            error_code = error.response.get("Error", {}).get("Code", "")
            raise LLMProviderError(
                retryable=error_code in RETRYABLE_BEDROCK_ERROR_CODES
            ) from error
        except EndpointConnectionError as error:
            raise LLMProviderError(retryable=True) from error
        except BotoCoreError as error:
            raise LLMProviderError(retryable=False) from error

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
                "bedrock-runtime", region_name=self._region_name
            )
        return self._runtime_client


def create_llm_client(settings: Settings) -> LLMClient:
    settings.validate_m4_alignment()
    return BedrockLLMClient(settings.llm_model_id, settings.aws_region)
