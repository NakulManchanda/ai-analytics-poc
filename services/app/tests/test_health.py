import httpx
import pytest
from app.main import create_app
from fastapi.testclient import TestClient


def test_health_returns_service_status() -> None:
    client = TestClient(create_app())

    response = client.get("/health")
    proxied_response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "ai-app"}
    assert proxied_response.status_code == 200
    assert proxied_response.json() == {"status": "ok", "service": "ai-app"}


def test_status_reports_successful_mcp_discovery(monkeypatch) -> None:
    from app.routers import status

    async def discovered_mcp(_: str) -> dict[str, int]:
        return {"tools": 0, "resources": 0}

    monkeypatch.setattr(status, "discover_mcp", discovered_mcp)
    client = TestClient(create_app())

    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json() == {
        "app": {"status": "ok", "service": "ai-app"},
        "mcp": {"status": "ok", "tools": 0, "resources": 0},
    }


def test_status_reports_unavailable_mcp_without_failing(monkeypatch) -> None:
    from app.routers import status

    async def unavailable_mcp(_: str) -> dict[str, int]:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(status, "discover_mcp", unavailable_mcp)
    client = TestClient(create_app())

    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json() == {
        "app": {"status": "ok", "service": "ai-app"},
        "mcp": {"status": "unavailable"},
    }


def test_status_reraises_unexpected_mcp_discovery_failure(monkeypatch) -> None:
    from app.routers import status

    async def unexpected_failure(_: str) -> dict[str, int]:
        raise ValueError("unexpected profile state")

    monkeypatch.setattr(status, "discover_mcp", unexpected_failure)
    client = TestClient(create_app())

    with pytest.raises(ValueError, match="unexpected profile state"):
        client.get("/api/status")
