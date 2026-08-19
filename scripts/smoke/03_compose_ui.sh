#!/usr/bin/env bash
set -euo pipefail

cleanup() {
  docker compose down --remove-orphans
}
trap cleanup EXIT

docker compose up --build -d

for attempt in $(seq 1 60); do
  status_json="$(curl --silent --show-error --fail http://localhost:3000/api/health 2>/dev/null || true)"
  if [[ -n "${status_json}" ]] && STATUS_JSON="${status_json}" python - <<'PY'
import json
import os

status = json.loads(os.environ["STATUS_JSON"])
assert status == {"status": "ok", "service": "ai-app"}
PY
  then
    curl --silent --show-error --fail http://localhost:3000/ | grep -q "Taxi analytics control room"
    echo "Compose UI smoke passed"
    exit 0
  fi
  sleep 1
done

docker compose logs --no-color >&2
echo "Compose UI smoke timed out" >&2
exit 1
