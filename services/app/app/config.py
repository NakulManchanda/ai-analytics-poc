import os
from dataclasses import dataclass

DEFAULT_MODEL_ID = "amazon.nova-micro-v1:0"


@dataclass(frozen=True)
class Settings:
    llm_provider: str = "bedrock"
    llm_model_id: str = DEFAULT_MODEL_ID
    aws_region: str | None = None

    @classmethod
    def from_environment(cls) -> "Settings":
        return cls(
            llm_provider=os.getenv("LLM_PROVIDER", "bedrock"),
            llm_model_id=os.getenv("LLM_MODEL_ID", DEFAULT_MODEL_ID),
            aws_region=os.getenv("AWS_REGION"),
        )
