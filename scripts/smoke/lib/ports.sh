#!/usr/bin/env bash

find_free_loopback_port() {
  local avoid_port="${1:-}"
  python3 - "${avoid_port}" <<'PY'
import socket
import sys

avoid = sys.argv[1]
for _ in range(32):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = str(sock.getsockname()[1])
    if port != avoid:
        print(port)
        raise SystemExit(0)
raise SystemExit("could not allocate a distinct loopback port")
PY
}

resolve_smoke_port() {
  local variable_name="$1"
  local avoid_port="${2:-}"
  local configured_port="${!variable_name:-${PORT:-}}"
  if [[ -n "${configured_port}" ]]; then
    printf '%s\n' "${configured_port}"
    return
  fi
  find_free_loopback_port "${avoid_port}"
}
