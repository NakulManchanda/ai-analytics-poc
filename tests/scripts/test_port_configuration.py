from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_host_facing_commands_have_service_specific_then_generic_port_overrides() -> (
    None
):
    makefile = (ROOT / "Makefile").read_text()

    assert "APP_PORT ?= $(or $(PORT),8080)" in makefile
    assert "MCP_PORT ?= $(or $(PORT),8001)" in makefile
    assert "WEB_PORT ?= $(or $(PORT),3000)" in makefile
    assert "uvicorn app.main:app --host 0.0.0.0 --port $(APP_PORT)" in makefile
    assert (
        "fastmcp run services/mcp/mcp_server/server.py --transport http "
        "--host 0.0.0.0 --port $(MCP_PORT)"
    ) in makefile


def test_smoke_scripts_export_an_isolated_compose_project_and_port_precedence() -> None:
    compose_smoke = (ROOT / "scripts/smoke/03_compose_ui.sh").read_text()
    paid_smoke = (ROOT / "scripts/smoke/04_m5_bedrock.sh").read_text()

    assert 'web_port="${WEB_PORT:-${PORT:-3000}}"' in compose_smoke
    assert 'export WEB_PORT="${web_port}"' in compose_smoke
    assert (
        "export COMPOSE_PROJECT_NAME="
        '"${COMPOSE_PROJECT_NAME:-ai-analytics-m5-smoke-${web_port}-$$}"'
    ) in compose_smoke
    assert 'mcp_port="${MCP_PORT:-${PORT:-8001}}"' in paid_smoke
