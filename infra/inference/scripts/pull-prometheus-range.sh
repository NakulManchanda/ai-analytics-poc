#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

if [[ "${1:-}" == "--help" ]]; then
  echo "Usage: pull-prometheus-range.sh RUN_ID [START END [STEP]]"
  echo "Runs the queries in observability/prometheus/evidence_queries.json against Prometheus query_range"
  echo "(through the loopback tunnel from 'make inference-tunnel', PROMETHEUS_URL default http://127.0.0.1:19090)"
  echo "and writes one raw JSON response per query to metrics/inference/<run_id>/prometheus_range/."
  echo "START/END: epoch seconds or RFC3339 (default: last 30 minutes). STEP default 15s."
  echo "Evidence scope: WINDOW-level time series; never attribute to a single request."
  exit 0
fi

RUN_ID="${1:?RUN_ID is required (see --help)}"
END="${3:-${END:-$(date +%s)}}"
START="${2:-${START:-$((END - 1800))}}"
STEP="${4:-${STEP:-15s}}"
PROMETHEUS_URL="${PROMETHEUS_URL:-http://127.0.0.1:19090}"
QUERIES="$INFERENCE_ROOT/observability/prometheus/evidence_queries.json"
OUT_DIR="$INFERENCE_ROOT/../../metrics/inference/$RUN_ID/prometheus_range"
curl -fsS --connect-timeout 5 "$PROMETHEUS_URL/-/ready" >/dev/null \
  || { echo "Prometheus not reachable at $PROMETHEUS_URL; run 'make inference-tunnel' first" >&2; exit 1; }

mkdir -p "$OUT_DIR"
echo "== Pulling Prometheus range for run $RUN_ID ($START .. $END, step $STEP) =="
python3 - "$QUERIES" <<'PY' | while IFS=$'\t' read -r name expr; do
import json, sys
for q in json.load(open(sys.argv[1]))["queries"]:
    print(q["name"], q["expr"], sep="\t")
PY
  out="$OUT_DIR/$name.json"
  curl -fsS --connect-timeout 5 --max-time 60 "$PROMETHEUS_URL/api/v1/query_range" \
    --data-urlencode "query=$expr" --data-urlencode "start=$START" \
    --data-urlencode "end=$END" --data-urlencode "step=$STEP" > "$out"
  python3 - "$out" "$name" <<'PY'
import json, sys
body = json.load(open(sys.argv[1]))
if body.get("status") != "success":
    sys.exit(f"query {sys.argv[2]} failed: {body}")
n = len(body["data"]["result"])
print(f"  {sys.argv[2]}: {n} series" + ("  (EMPTY: metric absent in window)" if n == 0 else ""))
PY
done

printf '{"run_id": "%s", "start": "%s", "end": "%s", "step": "%s", "prometheus_url": "%s", "evidence_scope": "WINDOW-level time series (Prometheus); not attributable to single requests"}\n' \
  "$RUN_ID" "$START" "$END" "$STEP" "$PROMETHEUS_URL" > "$OUT_DIR/_meta.json"
echo "Range export written to $OUT_DIR"
