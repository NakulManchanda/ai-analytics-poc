import pytest
from app.bedrock_smoke import validate_bedrock_smoke_payload


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
