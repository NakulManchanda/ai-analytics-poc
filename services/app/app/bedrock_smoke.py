from typing import Any

from app.config import DEFAULT_MODEL_ID

EXPECTED_ANSWER = "BEDROCK_SMOKE_OK"


def validate_bedrock_smoke_payload(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("response must be a JSON object")

    if payload.get("answer") != EXPECTED_ANSWER:
        raise ValueError("answer did not match the smoke contract")

    llm_call_id = payload.get("llm_call_id")
    if not isinstance(llm_call_id, str) or not llm_call_id:
        raise ValueError("llm_call_id must be a non-empty string")

    if payload.get("model_id") != DEFAULT_MODEL_ID:
        raise ValueError(f"model_id must equal {DEFAULT_MODEL_ID}")

    usage = payload.get("usage")
    if not isinstance(usage, dict):
        raise ValueError("usage must be a JSON object")

    for field_name in ("input_tokens", "output_tokens", "total_tokens"):
        value = usage.get(field_name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"usage.{field_name} must be a non-negative integer")

    if usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]:
        raise ValueError(
            "usage.total_tokens must equal input_tokens plus output_tokens"
        )

    latency_ms = payload.get("latency_ms")
    if (
        isinstance(latency_ms, bool)
        or not isinstance(latency_ms, int)
        or latency_ms < 0
    ):
        raise ValueError("latency_ms must be a non-negative integer")

    return payload
