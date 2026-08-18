from app.main import create_app
from fastapi.testclient import TestClient


class FakeLLMClient:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def ask(self, prompt: str) -> dict[str, object]:
        self.prompts.append(prompt)
        return {
            "text": "A short answer.",
            "model_id": "amazon.nova-micro-v1:0",
            "input_tokens": 4,
            "output_tokens": 3,
            "latency_ms": 17,
        }


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
