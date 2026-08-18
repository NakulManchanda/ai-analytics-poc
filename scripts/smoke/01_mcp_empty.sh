#!/usr/bin/env bash
set -euo pipefail

mcp_url="${MCP_URL:-http://localhost:8001/mcp}"
server_pid=""

cleanup() {
  if [[ -n "${server_pid}" ]]; then
    kill "${server_pid}" 2>/dev/null || true
    wait "${server_pid}" 2>/dev/null || true
  fi
}
trap cleanup EXIT

if ! curl --fail --silent --show-error --max-time 1 "${mcp_url}" >/dev/null 2>&1; then
  (
    cd services/mcp
    uv run --frozen fastmcp run mcp_server/server.py --transport http --host 127.0.0.1 --port 8001
  ) >/tmp/ai-analytics-mcp-smoke.log 2>&1 &
  server_pid=$!
fi

for _ in {1..30}; do
  if curl --fail --silent --show-error --max-time 1 "${mcp_url}" >/dev/null 2>&1; then
    break
  fi
  sleep 0.2
done

MCP_URL="${mcp_url}" uv run --project services/mcp python - <<'PY'
import asyncio
import os

from fastmcp import Client


async def verify() -> None:
    async with Client(os.environ["MCP_URL"]) as client:
        tools = await client.list_tools()
        resources = await client.list_resources()
    assert tools == [], tools
    assert resources == [], resources


asyncio.run(verify())
print("MCP empty protocol check passed: tools=[], resources=[]")
PY
