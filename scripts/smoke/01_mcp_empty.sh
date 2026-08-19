#!/usr/bin/env bash
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/ports.sh"

mcp_port="$(resolve_smoke_port MCP_PORT)"
mcp_url="${MCP_URL:-http://localhost:${mcp_port}/mcp}"
mcp_log="$(mktemp "${TMPDIR:-/tmp}/ai-analytics-mcp-smoke.XXXXXX")"
server_pid=""

protocol_ready() {
  MCP_URL="${mcp_url}" uv run --frozen --project services/mcp python - <<'PY' >/dev/null 2>&1
import asyncio
import os
import time

from fastmcp import Client


async def verify_connection() -> None:
    async with Client(os.environ["MCP_URL"]) as client:
        await client.list_tools()


async def wait_for_protocol() -> None:
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        try:
            await asyncio.wait_for(verify_connection(), timeout=0.5)
            return
        except Exception:
            await asyncio.sleep(0.2)
    raise RuntimeError("MCP protocol did not become ready")


asyncio.run(wait_for_protocol())
PY
}

cleanup() {
  if [[ -n "${server_pid}" ]]; then
    kill "${server_pid}" 2>/dev/null || true
    wait "${server_pid}" 2>/dev/null || true
  fi
  rm -f -- "${mcp_log}"
}
trap cleanup EXIT

if ! protocol_ready; then
  (
    cd services/mcp
    exec uv run --frozen fastmcp run mcp_server/server.py --transport http --host 127.0.0.1 --port "${mcp_port}"
  ) >"${mcp_log}" 2>&1 &
  server_pid=$!
fi

if ! protocol_ready; then
  echo "MCP protocol readiness timed out for ${mcp_url}; showing server diagnostics before cleanup:" >&2
  if [[ -f "${mcp_log}" ]]; then
    tail -n 40 "${mcp_log}" >&2
  fi
  exit 1
fi

MCP_URL="${mcp_url}" uv run --frozen --project services/mcp python - <<'PY'
import asyncio
import os

from fastmcp import Client


async def verify() -> None:
    async with Client(os.environ["MCP_URL"]) as client:
        tools = await client.list_tools()
        resources = await client.list_resources()
    assert [tool.name for tool in tools] == ["get_dataset_profile"], tools
    assert [str(resource.uri) for resource in resources] == ["dataset://nyc-taxi/schema"], resources


asyncio.run(verify())
print("MCP dataset protocol check passed: schema resource and profile tool are discoverable")
PY
