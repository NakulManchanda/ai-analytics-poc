import os
from dataclasses import dataclass

M4_AWS_REGION = "us-east-1"
DEFAULT_MODEL_ID = "amazon.nova-micro-v1:0"
M4_BEDROCK_MODEL_ARN = (
    f"arn:aws:bedrock:{M4_AWS_REGION}::foundation-model/{DEFAULT_MODEL_ID}"
)


class LLMConfigurationError(ValueError):
    """Raised when M4 configuration does not match its deployed IAM allowlist."""


@dataclass(frozen=True)
class Settings:
    llm_provider: str = "bedrock"
    llm_model_id: str = DEFAULT_MODEL_ID
    aws_region: str = M4_AWS_REGION

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            llm_provider=os.getenv("LLM_PROVIDER", "bedrock"),
            llm_model_id=os.getenv("LLM_MODEL_ID", DEFAULT_MODEL_ID),
            aws_region=os.getenv("AWS_REGION", M4_AWS_REGION),
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
