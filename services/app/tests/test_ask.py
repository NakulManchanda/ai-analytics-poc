from app.llm import BedrockLLMClient, LLMResult
from app.main import create_app
from fastapi.testclient import TestClient


class FakeLLMClient:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def ask(self, prompt: str) -> LLMResult:
        self.prompts.append(prompt)
        return LLMResult(
            text="A short answer.",
            model_id="amazon.nova-micro-v1:0",
            input_tokens=4,
            output_tokens=3,
            latency_ms=17,
        )


def test_ask_returns_the_fake_client_answer_and_usage_metadata() -> None:
    llm_client = FakeLLMClient()
    client = TestClient(create_app(llm_client=llm_client))

    response = client.post("/api/ask", json={"prompt": "Summarize this."})

    assert response.status_code == 200
    assert response.json() == {
        "answer": "A short answer.",
        "model_id": "amazon.nova-micro-v1:0",
        "usage": {"input_tokens": 4, "output_tokens": 3},
        "latency_ms": 17,
    }
    assert llm_client.prompts == ["Summarize this."]


def test_ask_rejects_a_whitespace_only_prompt_without_calling_the_client() -> None:
    llm_client = FakeLLMClient()
    client = TestClient(create_app(llm_client=llm_client))

    response = client.post("/api/ask", json={"prompt": "   "})

    assert response.status_code == 422
    assert llm_client.prompts == []


class FakeBedrockRuntimeClient:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def converse(self, **request: object) -> dict[str, object]:
        self.requests.append(request)
        return {
            "output": {"message": {"content": [{"text": "Bedrock answer."}]}},
            "usage": {"inputTokens": 6, "outputTokens": 4},
            "metrics": {"latencyMs": 23},
        }


def test_bedrock_client_maps_converse_response_and_uses_bounded_request() -> None:
    runtime_client = FakeBedrockRuntimeClient()
    llm_client = BedrockLLMClient(
        "amazon.nova-micro-v1:0", runtime_client=runtime_client
    )

    result = llm_client.ask("Give a concise answer.")

    assert result == LLMResult(
        text="Bedrock answer.",
        model_id="amazon.nova-micro-v1:0",
        input_tokens=6,
        output_tokens=4,
        latency_ms=23,
    )
    assert runtime_client.requests == [
        {
            "modelId": "amazon.nova-micro-v1:0",
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": "Give a concise answer."}],
                }
            ],
            "inferenceConfig": {"maxTokens": 128, "temperature": 0.0},
        }
    ]
