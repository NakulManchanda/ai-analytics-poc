import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_host_facing_commands_have_service_specific_then_generic_port_overrides() -> (
    None
):
    makefile = (ROOT / "Makefile").read_text()

    assert "APP_HOST_PORT := $(or $(APP_PORT),$(PORT),8080)" in makefile
    assert "MCP_HOST_PORT := $(or $(MCP_PORT),$(PORT),8001)" in makefile
    assert "uvicorn app.main:app --host 0.0.0.0 --port $(APP_HOST_PORT)" in makefile
    assert (
        "fastmcp run services/mcp/mcp_server/server.py --transport http "
        "--host 0.0.0.0 --port $(MCP_HOST_PORT)"
    ) in makefile
    assert "Ambiguous PORT for aggregate smoke" in makefile
    assert "bedrock-smoke" in makefile
    assert "python services/app/scripts/bedrock_smoke.py" in makefile

    assert "--port 8082" in subprocess.check_output(
        ["make", "-n", "APP_PORT=8082", "PORT=9000", "dev"],
        cwd=ROOT,
        text=True,
    )
    assert "--port 8002" in subprocess.check_output(
        ["make", "-n", "PORT=8002", "mcp-dev"], cwd=ROOT, text=True
    )
    ambiguous = subprocess.run(
        ["make", "PORT=9000", "smoke"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert ambiguous.returncode == 2
    assert "Ambiguous PORT for aggregate smoke" in ambiguous.stderr


def test_smoke_scripts_export_an_isolated_compose_project_and_port_precedence() -> None:
    compose_smoke = (ROOT / "scripts/smoke/03_compose_ui.sh").read_text()
    mcp_smoke = (ROOT / "scripts/smoke/01_mcp_empty.sh").read_text()
    paid_smoke = (ROOT / "scripts/smoke/04_m5_bedrock.sh").read_text()

    assert 'requested_web_port="${WEB_PORT:-${PORT:-0}}"' in compose_smoke
    assert 'export WEB_PORT="${requested_web_port}"' in compose_smoke
    assert (
        "export COMPOSE_PROJECT_NAME="
        '"${COMPOSE_PROJECT_NAME:-ai-analytics-m5-smoke-${requested_web_port}-$$}"'
    ) in compose_smoke
    assert 'source "$(dirname "${BASH_SOURCE[0]}")/lib/ports.sh"' in mcp_smoke
    assert 'mcp_port="$(resolve_smoke_port MCP_PORT)"' in mcp_smoke
    assert 'mcp_port="$(resolve_smoke_port MCP_PORT)"' in paid_smoke


def test_ephemeral_port_helper_can_derive_distinct_ports_for_parallel_smokes() -> None:
    helper = ROOT / "scripts/smoke/lib/ports.sh"
    result = subprocess.check_output(
        [
            "bash",
            "-c",
            'source "$1"; first="$(resolve_smoke_port MCP_PORT)"; '
            'second="$(resolve_smoke_port MCP_PORT "$first")"; '
            'test "$first" != "$second"; printf "%s %s" "$first" "$second"',
            "bash",
            str(helper),
        ],
        text=True,
    )
    first, second = result.split()
    assert first != second
