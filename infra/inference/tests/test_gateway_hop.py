"""Hop policy cannot trust client connector parameters or placement hints as proof."""

from types import SimpleNamespace

from infra.inference.gateway.hop import prepare

HEADERS = {"x-request-id": "r", "x-conversation-id": "c", "x-agent-step": "2"}
DECISION = SimpleNamespace(
    prior_worker="worker_a", chosen_worker="worker_b", prior_reusable_tokens=2000
)


def test_disabled_strips_external_connector_control(monkeypatch):
    monkeypatch.delenv("KV_HOP_ENABLED", raising=False)
    body = {"messages": [], "kv_transfer_params": {"lmcache.hop.load": True}}
    payload, event = prepare(body, DECISION, HEADERS, None)
    assert "kv_transfer_params" not in payload
    assert event["hop_result"] == "not_attempted"
    assert "kv_transfer_params" in body


def test_enabled_owns_parameters_and_preserves_correlation(monkeypatch):
    monkeypatch.setenv("KV_HOP_ENABLED", "1")
    payload, event = prepare({"kv_transfer_params": {"evil": "tag"}}, DECISION, HEADERS, 3)
    p = payload["kv_transfer_params"]
    assert p["lmcache.hop.load"] is True
    assert p["lmcache.hop.request_id"] == "r"
    assert "evil" not in p
    assert event["hop_result"] == "pending"


def test_same_worker_belief_does_not_request_external_load(monkeypatch):
    monkeypatch.setenv("KV_HOP_ENABLED", "1")
    same_worker = SimpleNamespace(
        prior_worker="worker_a", chosen_worker="worker_a", prior_reusable_tokens=2000
    )

    payload, event = prepare({}, same_worker, HEADERS, 3)

    assert payload["kv_transfer_params"]["lmcache.hop.load"] is False
    assert event == {"hop_decision_reason": "local_prefix_present", "hop_result": "recompute"}


def test_no_prior_worker_does_not_request_external_load(monkeypatch):
    monkeypatch.setenv("KV_HOP_ENABLED", "1")
    no_prior = SimpleNamespace(
        prior_worker=None, chosen_worker="worker_a", prior_reusable_tokens=0
    )

    payload, event = prepare({}, no_prior, HEADERS, 3)

    assert payload["kv_transfer_params"]["lmcache.hop.load"] is False
    assert event == {"hop_decision_reason": "no_prior_worker", "hop_result": "recompute"}


def test_deadline_selects_recompute_and_override_is_gated(monkeypatch):
    monkeypatch.setenv("KV_HOP_ENABLED", "1")
    monkeypatch.delenv("ALLOW_EXPERIMENT_CONTROLS", raising=False)
    payload, _ = prepare({}, DECISION, HEADERS, 3, "off")
    assert payload["kv_transfer_params"]["lmcache.hop.load"] is True
    monkeypatch.setenv("ALLOW_EXPERIMENT_CONTROLS", "1")
    payload, _ = prepare({}, DECISION, HEADERS, 3, "off")
    assert payload["kv_transfer_params"]["lmcache.hop.load"] is False
    payload, event = prepare({}, DECISION, HEADERS, 0.1, "on")
    assert payload["kv_transfer_params"]["lmcache.hop.load"] is False
    assert event["hop_decision_reason"] == "deadline_recompute"


def test_experiment_case_only_changes_bounded_reason_when_controls_enabled(monkeypatch):
    monkeypatch.setenv("KV_HOP_ENABLED", "1")
    monkeypatch.setenv("ALLOW_EXPERIMENT_CONTROLS", "1")
    payload, event = prepare(
        {},
        DECISION,
        HEADERS,
        3,
        "off",
        "cross_worker_recompute_transfer_disabled",
    )
    assert payload["kv_transfer_params"]["lmcache.hop.load"] is False
    assert event["hop_decision_reason"] == "transfer_disabled"

    _, event = prepare({}, DECISION, HEADERS, 3, "off", "untrusted-reason")
    assert event["hop_decision_reason"] == "experiment_off"


def _remote(tokens: int) -> SimpleNamespace:
    return SimpleNamespace(
        prior_worker="worker_a", chosen_worker="worker_b", prior_reusable_tokens=tokens
    )


def test_small_prefix_is_recomputed_not_moved(monkeypatch):
    monkeypatch.setenv("KV_HOP_ENABLED", "1")
    monkeypatch.delenv("KV_HOP_MIN_TOKENS", raising=False)
    payload, event = prepare({}, _remote(223), HEADERS, 3)  # the app's ~223-token global prefix
    assert payload["kv_transfer_params"]["lmcache.hop.load"] is False
    assert event == {"hop_decision_reason": "below_min_tokens", "hop_result": "recompute"}


def test_prefix_at_the_minimum_is_moved(monkeypatch):
    monkeypatch.setenv("KV_HOP_ENABLED", "1")
    monkeypatch.delenv("KV_HOP_MIN_TOKENS", raising=False)
    below, _ = prepare({}, _remote(1023), HEADERS, 3)
    at, event = prepare({}, _remote(1024), HEADERS, 3)
    assert below["kv_transfer_params"]["lmcache.hop.load"] is False
    assert at["kv_transfer_params"]["lmcache.hop.load"] is True
    assert event["hop_decision_reason"] == "remote_prefix_candidate"


def test_minimum_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("KV_HOP_ENABLED", "1")
    monkeypatch.setenv("KV_HOP_MIN_TOKENS", "4096")
    payload, event = prepare({}, _remote(2000), HEADERS, 3)
    assert payload["kv_transfer_params"]["lmcache.hop.load"] is False
    assert event["hop_decision_reason"] == "below_min_tokens"
    monkeypatch.setenv("KV_HOP_MIN_TOKENS", "0")
    payload, _ = prepare({}, _remote(1), HEADERS, 3)
    assert payload["kv_transfer_params"]["lmcache.hop.load"] is True


def test_deadline_still_wins_over_a_large_prefix(monkeypatch):
    monkeypatch.setenv("KV_HOP_ENABLED", "1")
    payload, event = prepare({}, _remote(5000), HEADERS, 0.5)
    assert payload["kv_transfer_params"]["lmcache.hop.load"] is False
    assert event["hop_decision_reason"] == "deadline_recompute"


def test_explicit_experiment_override_bypasses_the_minimum(monkeypatch):
    # The E5 proof forces the hop at every size, including prefixes below the production minimum.
    monkeypatch.setenv("KV_HOP_ENABLED", "1")
    monkeypatch.setenv("ALLOW_EXPERIMENT_CONTROLS", "1")
    payload, event = prepare({}, _remote(100), HEADERS, 3, "on")
    assert payload["kv_transfer_params"]["lmcache.hop.load"] is True
    assert event["hop_decision_reason"] == "experiment_on"
