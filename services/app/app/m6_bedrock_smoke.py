from typing import Any

from app.config import DEFAULT_MODEL_ID


def _validate_usage(payload: object) -> dict[str, int]:
    if not isinstance(payload, dict):
        raise ValueError("usage must be a JSON object")
    usage: dict[str, int] = {}
    for field_name in ("input_tokens", "output_tokens", "total_tokens"):
        value = payload.get(field_name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"usage.{field_name} must be a non-negative integer")
        usage[field_name] = value
    if usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]:
        raise ValueError(
            "usage.total_tokens must equal input_tokens plus output_tokens"
        )
    return usage


def _validate_llm_call(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("each llm call must be a JSON object")
    if not isinstance(payload.get("llm_call_id"), str) or not payload["llm_call_id"]:
        raise ValueError("llm_call_id must be a non-empty string")
    if payload.get("model_id") != DEFAULT_MODEL_ID:
        raise ValueError(f"model_id must equal {DEFAULT_MODEL_ID}")
    latency_ms = payload.get("latency_ms")
    if (
        isinstance(latency_ms, bool)
        or not isinstance(latency_ms, int)
        or latency_ms < 0
    ):
        raise ValueError("latency_ms must be a non-negative integer")
    return {**payload, "usage": _validate_usage(payload.get("usage"))}


def validate_m6_bedrock_smoke_payload(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("response must be a JSON object")
    answer = payload.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("answer must be a non-empty string")
    if not isinstance(payload.get("tool_call_id"), str) or not payload["tool_call_id"]:
        raise ValueError("tool_call_id must be a non-empty string")
    if not isinstance(payload.get("query_id"), str) or not payload["query_id"]:
        raise ValueError("query_id must be a non-empty string")
    llm_calls = payload.get("llm_calls")
    if not isinstance(llm_calls, list) or len(llm_calls) != 2:
        raise ValueError("M6 requires exactly two LLM calls")
    calls = [_validate_llm_call(call) for call in llm_calls]
    usage = _validate_usage(payload.get("usage"))
    if usage != {
        "input_tokens": sum(call["usage"]["input_tokens"] for call in calls),
        "output_tokens": sum(call["usage"]["output_tokens"] for call in calls),
        "total_tokens": sum(call["usage"]["total_tokens"] for call in calls),
    }:
        raise ValueError("aggregate usage must equal the two LLM call usages")
    latency_ms = payload.get("latency_ms")
    if latency_ms != sum(call["latency_ms"] for call in calls):
        raise ValueError("aggregate latency must equal the two LLM call latencies")
    return payload
