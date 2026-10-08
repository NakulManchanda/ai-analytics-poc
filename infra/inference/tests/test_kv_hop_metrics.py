from prometheus_client import generate_latest

from infra.inference.kv_transfer import metrics


def test_metrics_are_bounded_and_exclude_request_identity() -> None:
    metrics.record(
        {
            "hop_result": "transferred",
            "hop_decision_reason": "remote_prefix_candidate",
            "transferred_tokens": 256,
            "transferred_bytes": 4096,
            "transfer_ms": 12,
            "request_id": "must-not-be-a-label",
        }
    )
    rendered = generate_latest().decode()
    assert 'hop_total{reason="remote_prefix_candidate",result="transferred"}' in rendered
    assert "must-not-be-a-label" not in rendered


def test_untrusted_reason_is_collapsed() -> None:
    metrics.record({"hop_result": "invented", "hop_decision_reason": "tenant-secret"})
    rendered = generate_latest().decode()
    assert 'hop_total{reason="unspecified",result="failed"}' in rendered
    assert "tenant-secret" not in rendered


def test_intermediate_available_event_does_not_double_count() -> None:
    initial = metrics.HOP.labels("transferred", "experiment_on")._value.get()
    metrics.record(
        {
            "event": "kv_hop_available",
            "hop_result": "transferred",
            "hop_decision_reason": "experiment_on",
            "transferred_tokens": 128,
            "transferred_bytes": 2048,
        }
    )
    after_available = metrics.HOP.labels("transferred", "experiment_on")._value.get()
    assert after_available == initial

    metrics.record(
        {
            "event": "kv_hop",
            "hop_result": "transferred",
            "hop_decision_reason": "experiment_on",
            "transferred_tokens": 128,
            "transferred_bytes": 2048,
        }
    )
    after_final = metrics.HOP.labels("transferred", "experiment_on")._value.get()
    assert after_final == initial + 1
