import pytest
from app.bedrock_smoke import validate_bedrock_smoke_payload
from app.m5_bedrock_smoke import validate_m5_bedrock_smoke_payload


def test_bedrock_smoke_payload_accepts_the_expected_complete_response() -> None:
    payload = {
        "answer": "BEDROCK_SMOKE_OK",
        "llm_call_id": "llm_call_smoke",
        "model_id": "amazon.nova-micro-v1:0",
        "usage": {"input_tokens": 11, "output_tokens": 8, "total_tokens": 19},
        "latency_ms": 322,
    }

    assert validate_bedrock_smoke_payload(payload) == payload


@pytest.mark.parametrize(
    "payload",
    [
        {"answer": "unexpected"},
        {
            "answer": "BEDROCK_SMOKE_OK",
            "llm_call_id": "llm_call_smoke",
            "model_id": "amazon.nova-lite-v1:0",
            "usage": {"input_tokens": 11, "output_tokens": 8, "total_tokens": 19},
            "latency_ms": 322,
        },
        {
            "answer": "BEDROCK_SMOKE_OK",
            "llm_call_id": "llm_call_smoke",
            "model_id": "amazon.nova-micro-v1:0",
            "usage": {"input_tokens": 11, "output_tokens": 8, "total_tokens": 18},
            "latency_ms": 322,
        },
        {
            "answer": "BEDROCK_SMOKE_OK",
            "llm_call_id": "llm_call_smoke",
            "model_id": "amazon.nova-micro-v1:0",
            "usage": {"input_tokens": "11", "output_tokens": 8, "total_tokens": 19},
            "latency_ms": 322,
        },
    ],
)
def test_bedrock_smoke_payload_rejects_missing_or_malformed_required_data(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        validate_bedrock_smoke_payload(payload)


def test_m5_bedrock_smoke_payload_requires_two_llm_calls_and_one_profile_tool() -> None:
    payload = {
        "answer": "M5_BEDROCK_SMOKE_OK",
        "tool_call_id": "tool_smoke",
        "llm_calls": [
            {
                "llm_call_id": "llm_proposal",
                "model_id": "amazon.nova-micro-v1:0",
                "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
                "latency_ms": 17,
            },
            {
                "llm_call_id": "llm_answer",
                "model_id": "amazon.nova-micro-v1:0",
                "usage": {"input_tokens": 20, "output_tokens": 3, "total_tokens": 23},
                "latency_ms": 19,
            },
        ],
        "usage": {"input_tokens": 30, "output_tokens": 5, "total_tokens": 35},
        "latency_ms": 36,
    }

    assert validate_m5_bedrock_smoke_payload(payload) == payload


@pytest.mark.parametrize(
    "payload",
    [
        {"answer": "unexpected"},
        {
            "answer": "M5_BEDROCK_SMOKE_OK",
            "tool_call_id": "tool_smoke",
            "llm_calls": [],
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            "latency_ms": 0,
        },
    ],
)
def test_m5_bedrock_smoke_payload_fails_closed_for_incomplete_sequence(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        validate_m5_bedrock_smoke_payload(payload)
