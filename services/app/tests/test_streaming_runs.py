from types import SimpleNamespace

from app.main import create_app
from fastapi.testclient import TestClient


def test_run_first_endpoint_returns_before_dispatched_execution() -> None:
    executed: list[str] = []
    pending_tasks: list[object] = []

    class RunFirstLoop:
        def prepare_run(
            self, prompt: str, conversation_id: str | None = None
        ) -> SimpleNamespace:
            assert prompt == "Which pickup zones lead?"
            assert conversation_id is None
            return SimpleNamespace(
                prompt=prompt,
                conversation_id="conv_stream_1",
                message_id="msg_stream_1",
                run_id="run_stream_1",
            )

        def execute(self, submission: SimpleNamespace) -> None:
            executed.append(submission.run_id)

    client = TestClient(
        create_app(
            orchestration_loop=RunFirstLoop(),  # type: ignore[arg-type]
            run_dispatcher=pending_tasks.append,
        )
    )

    response = client.post(
        "/api/runs", json={"prompt": "Which pickup zones lead?"}
    )

    assert response.status_code == 202
    assert response.json() == {
        "conversation_id": "conv_stream_1",
        "message_id": "msg_stream_1",
        "run_id": "run_stream_1",
        "events_url": "/api/runs/run_stream_1/events",
    }
    assert executed == []
    assert len(pending_tasks) == 1

    pending_tasks[0]()  # type: ignore[operator]
    assert executed == ["run_stream_1"]
