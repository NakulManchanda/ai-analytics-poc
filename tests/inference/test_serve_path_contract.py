"""Contract tests for Issue #121 Serve Path and correlation metadata."""

import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from app.prefix import build_query_proposal_partition
from fastapi.testclient import TestClient

from infra.inference.gateway import main as gateway_main
from infra.inference.gateway.main import app as gateway_app
from infra.inference.gateway.workers import Registry, Worker

ROOT = Path(__file__).resolve().parents[2]


def test_prefix_id_consistency_across_contract_and_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No lifespan in TestClient, so seed a fresh healthy worker snapshot.
    reg = Registry([Worker("worker_a", "http://worker-a:8000")])
    snap = reg.snapshots["worker_a"]
    snap.healthy, snap.observed_at = True, time.monotonic()
    monkeypatch.setattr(gateway_main, "registry", reg)
    schema = {"columns": ["pickup_zone", "trip_count"]}
    part = build_query_proposal_partition("Top zones", schema)

    client = TestClient(gateway_app)
    mock_upstream = httpx.Response(
        200,
        json={
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        },
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_upstream
        resp = client.post(
            "/serve",
            json={
                "model": "Qwen/Qwen3-0.6B",
                "messages": [{"role": "user", "content": part.unique_suffix}],
            },
            headers={
                "x-request-id": "req-1",
                "x-conversation-id": "conv-1",
                "x-agent-step": "1",
                "x-prefix-id": part.prefix_id,
                "x-estimated-prompt-tokens": str(part.estimated_total_tokens),
            },
        )
        assert resp.status_code == 200
        assert resp.headers["x-prefix-id"] == part.prefix_id
        assert resp.headers["x-agent-step"] == "1"
        assert resp.headers["x-orchestration-stage"] == "worker_dispatch"


def test_tunnel_script_includes_gateway_forwarding() -> None:
    tunnel_script = ROOT / "infra" / "inference" / "scripts" / "tunnel.sh"
    assert tunnel_script.is_file()
    content = tunnel_script.read_text(encoding="utf-8")
    assert "18080:127.0.0.1:8080" in content
    assert "svc/inference-gateway 8080:8080" in content
