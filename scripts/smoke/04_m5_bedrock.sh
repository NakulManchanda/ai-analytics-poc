#!/usr/bin/env bash
set -euo pipefail

if [[ "${RUN_BEDROCK_SMOKE:-}" != "1" ]]; then
  echo "Set RUN_BEDROCK_SMOKE=1 to permit the paid M5 Bedrock smoke call." >&2
  exit 1
fi

mcp_port="${MCP_PORT:-${PORT:-8001}}"
mcp_url="${MCP_URL:-http://localhost:${mcp_port}/mcp}"
mcp_log="$(mktemp "${TMPDIR:-/tmp}/ai-analytics-m5-mcp.XXXXXX")"
server_pid=""

cleanup() {
  if [[ -n "${server_pid}" ]]; then
    kill "${server_pid}" 2>/dev/null || true
    wait "${server_pid}" 2>/dev/null || true
  fi
  rm -f -- "${mcp_log}"
}
trap cleanup EXIT

(
  cd services/mcp
  exec uv run --frozen fastmcp run mcp_server/server.py --transport http --host 127.0.0.1 --port "${mcp_port}"
) >"${mcp_log}" 2>&1 &
server_pid=$!

for attempt in $(seq 1 60); do
  if MCP_URL="${mcp_url}" uv run --frozen --project services/mcp python - <<'PY' >/dev/null 2>&1
import asyncio
import os

from fastmcp import Client


async def verify() -> None:
    async with Client(os.environ["MCP_URL"]) as client:
        profile = await client.call_tool("get_dataset_profile")
    assert isinstance(profile.data, dict)


asyncio.run(verify())
PY
  then
    MCP_URL="${mcp_url}" uv run --frozen --project services/app python services/app/scripts/m5_bedrock_smoke.py
    exit 0
  fi
  sleep 1
done

tail -n 80 "${mcp_log}" >&2
echo "M5 MCP readiness timed out" >&2
exit 1
