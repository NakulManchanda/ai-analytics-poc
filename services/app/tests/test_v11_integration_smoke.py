"""Local v1.1 API contract smoke coverage.

This deliberately uses the in-memory repository: it proves the API contract and
reloading a newly-created app over the same local state store.  It does not
claim process-restart durability, which is the separately deployed DynamoDB
checkpoint.
"""

from __future__ import annotations

import json
from typing import Any

from app.events import InMemoryEventPublisher
from app.llm import LocalFakeLLMClient
from app.main import create_app
from app.orchestration import OrchestrationLoop
from app.state import InMemoryStateRepository
from fastapi.testclient import TestClient


class IntegrationMCPClient:
    """A deterministic tool boundary for the two-turn API smoke."""

    def get_dataset_schema(self) -> dict[str, Any]:
        return {
            "dataset": "nyc-taxi",
            "month": "2024-01",
            "columns": ["PULocationID", "trip_distance", "fare_amount"],
        }

    def query_taxi_data(self, *, analysis: str, limit: int) -> dict[str, Any]:
        assert analysis == "top_pickup_zones"
        assert limit == 5
        return {
            "columns": ["pickup_zone", "trip_count"],
            "rows": [["JFK Airport", 42]],
            "row_count": 1,
            "execution_duration_ms": 12,
            "query_id": "qry_v11_integration",
            "truncated": False,
        }


def _sse_frames(response_text: str) -> list[dict[str, Any]]:
    return [
        json.loads(
            next(
                line.removeprefix("data: ")
                for line in frame.splitlines()
                if line.startswith("data: ")
            )
        )
        for frame in response_text.strip().split("\n\n")
        if "data: " in frame
    ]


def test_v11_local_api_smoke_recovers_two_turn_conversation_and_sse_contract() -> None:
    repository = InMemoryStateRepository()
    publisher = InMemoryEventPublisher()
    loop = OrchestrationLoop(
        llm_client=LocalFakeLLMClient(),
        mcp_client=IntegrationMCPClient(),
        state_repository=repository,
        event_publisher=publisher,
    )

    with TestClient(
        create_app(state_repository=repository, orchestration_loop=loop)
    ) as client:
        first_response = client.post(
            "/api/ask", json={"prompt": "Which pickup zone has the most trips?"}
        )
        assert first_response.status_code == 200
        first = first_response.json()

        second_response = client.post(
            "/api/ask",
            json={
                "conversation_id": first["conversation_id"],
                "prompt": "Show me the top five.",
            },
        )
        assert second_response.status_code == 200
        second = second_response.json()

    # A new app instance must reconstruct from the repository rather than live
    # event memory.  The in-memory repository remains explicit for local tests.
    with TestClient(create_app(state_repository=repository)) as recovered_client:
        conversation_response = recovered_client.get(
            f"/api/conversations/{first['conversation_id']}"
        )
        events_response = recovered_client.get(f"/api/runs/{second['run_id']}/events")

    assert second["conversation_id"] == first["conversation_id"]
    assert second["run_id"] != first["run_id"]
    assert conversation_response.status_code == 200
    conversation = conversation_response.json()
    assert [message["role"] for message in conversation["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert [run["run_id"] for run in conversation["runs"]] == [
        first["run_id"],
        second["run_id"],
    ]
    assert all(run["steps"] for run in conversation["runs"])

    assert events_response.status_code == 200
    assert "text/event-stream" in events_response.headers["content-type"]
    frames = _sse_frames(events_response.text)
    assert [frame["sequence"] for frame in frames] == list(range(1, len(frames) + 1))
    assert [frame["event_type"] for frame in frames] == [
        "run.received",
        "step.llm_proposal",
        "step.tool_call",
        "context.reduced",
        "step.llm_final_answer",
        "run.completed",
    ]
    assert frames[3]["payload"]["working_context"]["stored_message_count"] == 3
    assert frames[3]["payload"]["working_context"]["included_message_count"] == 3
    assert frames[-1]["payload"]["status"] == "completed"
    assert frames[-1]["payload"]["total_tokens"] > 0
    assert frames[-1]["payload"]["telemetry"]["ttft"] == {
        "available": False,
        "reason": "non_streaming_blocking",
    }
