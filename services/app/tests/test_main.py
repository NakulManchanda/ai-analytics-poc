from unittest.mock import sentinel

from app import main


def test_create_app_injects_the_supplied_tracer(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class RecordingLoop:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(main, "OrchestrationLoop", RecordingLoop)

    main.create_app(tracer=sentinel.tracer)

    assert captured["tracer"] is sentinel.tracer
