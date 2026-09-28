import os
from dataclasses import dataclass

M4_AWS_REGION = "us-east-1"
DEFAULT_MODEL_ID = "amazon.nova-micro-v1:0"
M4_BEDROCK_MODEL_ARN = (
    f"arn:aws:bedrock:{M4_AWS_REGION}::foundation-model/{DEFAULT_MODEL_ID}"
)


DEFAULT_INFERENCE_MODEL_ID = "Qwen/Qwen3-0.6B"
DEFAULT_INFERENCE_GATEWAY_URL = "http://127.0.0.1:18080/serve"


@dataclass(frozen=True)
class VoiceSettings:
    """Configuration for voice output (TTS) synthesis."""

    enabled: bool = True
    provider: str = "polly"
    voice_name: str = "Joanna"
    language: str = "en-US"
    streaming: bool = False

    @classmethod
    def from_environment(cls) -> "VoiceSettings":
        """Load voice settings from environment variables."""
        return cls(
            enabled=os.getenv("VOICE_ENABLED", "true").lower() in ("true", "1", "yes"),
            provider=os.getenv("VOICE_PROVIDER", "polly"),
            voice_name=os.getenv("VOICE_VOICE_NAME", os.getenv("VOICE_NAME", "Joanna")),
            language=os.getenv("VOICE_LANGUAGE", "en-US"),
            streaming=os.getenv("VOICE_STREAMING", "false").lower()
            in ("true", "1", "yes"),
        )


class LLMConfigurationError(ValueError):
    """Raised when LLM configuration does not match its required provider environment."""


@dataclass(frozen=True)
class Settings:
    llm_provider: str = "bedrock"
    llm_model_id: str = DEFAULT_MODEL_ID
    aws_region: str = M4_AWS_REGION
    dynamodb_table_name: str | None = None
    global_bedrock_monthly_limit_usd: str = "5.00"
    inference_gateway_url: str = DEFAULT_INFERENCE_GATEWAY_URL
    inference_model_id: str = DEFAULT_INFERENCE_MODEL_ID
    # D2: Qwen3 thinking is off for structured calls unconditionally; the final answer
    # call is off by default too, behind this flag, so #123 can compare on/off.
    inference_answer_thinking: bool = False

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            llm_provider=os.getenv("LLM_PROVIDER", "bedrock"),
            llm_model_id=os.getenv("LLM_MODEL_ID", DEFAULT_MODEL_ID),
            aws_region=os.getenv("AWS_REGION", M4_AWS_REGION),
            dynamodb_table_name=os.getenv("DYNAMODB_TABLE_NAME"),
            global_bedrock_monthly_limit_usd=os.getenv(
                "GLOBAL_BEDROCK_MONTHLY_LIMIT_USD", "5.00"
            ),
            inference_gateway_url=os.getenv(
                "INFERENCE_GATEWAY_URL",
                os.getenv("SERVE_URL", DEFAULT_INFERENCE_GATEWAY_URL),
            ),
            inference_model_id=os.getenv(
                "INFERENCE_MODEL_ID", DEFAULT_INFERENCE_MODEL_ID
            ),
            inference_answer_thinking=os.getenv(
                "INFERENCE_ANSWER_THINKING", "false"
            ).lower()
            in ("true", "1", "yes"),
        )

    def validate_m4_alignment(self) -> None:
        if self.llm_provider != "bedrock":
            raise LLMConfigurationError("M4 requires LLM_PROVIDER=bedrock")
        configured_model_arn = (
            f"arn:aws:bedrock:{self.aws_region}::foundation-model/{self.llm_model_id}"
        )
        if configured_model_arn != M4_BEDROCK_MODEL_ARN:
            raise LLMConfigurationError(
                "M4 requires the us-east-1 IAM-allowlisted amazon.nova-micro-v1:0 model"
            )

    def validate_inference_alignment(self) -> None:
        if self.llm_provider not in ("vllm", "serve"):
            raise LLMConfigurationError(
                f"Inference cluster requires LLM_PROVIDER=vllm or serve (got {self.llm_provider!r})"
            )
        if not self.inference_gateway_url.startswith(("http://", "https://")):
            raise LLMConfigurationError(
                "INFERENCE_GATEWAY_URL must be a valid HTTP(S) URL, "
                f"got {self.inference_gateway_url!r}"
            )

        if not self.inference_model_id:
            raise LLMConfigurationError("INFERENCE_MODEL_ID must be specified")
