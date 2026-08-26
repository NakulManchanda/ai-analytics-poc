from __future__ import annotations

import pytest
from app.events.publisher import InMemoryEventPublisher
from app.main import create_app
from app.orchestration import OrchestrationLoop
from app.state.models import Conversation, Run
from app.state.repository import InMemoryStateRepository
from fastapi.testclient import TestClient


class FakePublisher(InMemoryEventPublisher):
    def __init__(self) -> None:
        super().__init__()
        self.flags: dict[str, str] = {}

    def _get_client(self) -> FakePublisher:
        return self

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.flags[key] = value

    def get(self, key: str) -> str | None:
        return self.flags.get(key)


def test_cancel_run_success() -> None:
    repo = InMemoryStateRepository()
    pub = FakePublisher()
    conv = Conversation(conversation_id="conv_123")
    repo.create_conversation(conv)
    run = Run(
        run_id="run_123",
        conversation_id="conv_123",
        status="in_progress",
    )
    repo.create_run(run)

    loop = OrchestrationLoop(
        state_repository=repo,
        event_publisher=pub,
    )
    app = create_app(orchestration_loop=loop)
    client = TestClient(app)

    response = client.post("/api/runs/run_123/cancel")
    assert response.status_code == 202
    assert response.json() == {
        "run_id": "run_123",
        "status": "cancel_requested",
    }

    # Verify durable state update
    updated = repo.get_run("run_123")
    assert updated is not None
    assert updated.status == "cancel_requested"

    # Verify Redis cancel flag
    assert pub.get("run:cancel:run_123") == "1"

    # Verify is_cancelled helper
    assert loop.is_cancelled("run_123") is True

    # Verify event published
    events = pub.get_events_for_run("run_123")
    assert any(e.event_type == "run.cancel_requested" for e in events)


def test_cancel_run_not_found() -> None:
    repo = InMemoryStateRepository()
    loop = OrchestrationLoop(state_repository=repo)
    app = create_app(orchestration_loop=loop)
    client = TestClient(app)

    response = client.post("/api/runs/run_non_existent/cancel")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "run_not_found"


@pytest.mark.parametrize(
    "terminal_status", ["completed", "failed", "budget_exceeded", "cancelled"]
)
def test_cancel_run_already_terminal(terminal_status: str) -> None:
    repo = InMemoryStateRepository()
    conv = Conversation(conversation_id="conv_123")
    repo.create_conversation(conv)
    run = Run(
        run_id="run_123",
        conversation_id="conv_123",
        status=terminal_status,
    )
    repo.create_run(run)

    loop = OrchestrationLoop(state_repository=repo)
    app = create_app(orchestration_loop=loop)
    client = TestClient(app)

    response = client.post("/api/runs/run_123/cancel")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "run_already_terminal"
