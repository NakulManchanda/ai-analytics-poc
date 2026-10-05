#!/usr/bin/env bash
# SESSION: begin with `make inference-tmux-start` (tmux session with windows run / tunnel / watch), run `make inference-e1` in the
# run window, and finish with `make inference-tmux-end` (kills the session, the tunnel and leftover port-forward loops on the instance).
# =====================================================================================================================
# E1: cold vs declared-warm worker. Run it as:  make inference-e1   (needs `make inference-tunnel` open in another terminal)
# =====================================================================================================================
#
# THE QUESTION
#   After a worker restarts, is its very first request slower than the requests that follow once it has been running a while?
#   (The model is loaded and CUDA graphs are captured at startup, so any "cold" penalty here is small; a small or zero
#   difference is a valid result, not a failure.)
#
# THE STORY, IN ORDER  (this script is just these steps chained; each prints a "== n/6 ==" banner)
#
#   t0  start      load the E1 manifest (revisions, engine flags, RUN_ID), check gateway + both workers answer /health,
#                  ask "restart BOTH workers?" (YES=1 skips), remember START_EPOCH for the Prometheus window.
#   1/6 restart    restart worker B, then worker A (restart-test.sh). Each takes ~100 s: old pod dies, new pod loads the model,
#                  readiness passes, a smoke probe hits /v1/models.
#   2/6 re-attach  poll /health on 18001 and 18002 until 200. The tunnel reaches workers through `kubectl port-forward svc/...`,
#                  which dies with the old pod and re-attaches about a second later; a cold run started too early fails.
#   3/6 cold run   warmup.py sends 1 "unwarmed" request + 5 warm rounds to EACH worker directly. The first request, sent
#                  right after the restart, is the cold sample. The script aborts if this run measured nothing ({}).
#   4/6 wait       WARM_WAIT_S (default 300 s) of no traffic: the "declared warm" gap. Nothing else may hit the workers.
#   ... warm run   the same warmup.py again. Compare its summary with the cold one.
#   5/6 range      make inference-pull-range over START_EPOCH..END_EPOCH: the Prometheus time series for the whole story.
#   6/6 snapshot   pull-evidence.sh: one cluster snapshot (pods, logs, nvidia-smi, raw /metrics) taken after the runs.
#
# WHAT YOU SHOULD SEE IN PROMETHEUS / GRAFANA WHILE IT RUNS  (http://127.0.0.1:13000 and http://127.0.0.1:19090/graph,
# time range = last hour, tunnel required; shapes below are EXPECTATIONS from the design, not yet confirmed for E1 unless noted)
#   restart   Cluster & DCGM: "vLLM Workers Up" drops to 0 (or the series vanishes) for about 100 s per worker, then returns.
#             "Pods by phase" shows a short blip; the "Restarts"/pod series switch to a NEW pod name.
#             Router & Placement: "Worker health" leaves the healthy state while the worker is down; "Worker warm" shows its
#             warm flag for the returning worker.
#   cold/warm Prefill vs Decode, "TTFT p50/p95 per worker" = engine-side TTFT with no tunnel latency. Paste this into the
#             Prometheus Graph box (the name "vllm_ttft_p95" in evidence_queries.json is NOT a metric):
#               histogram_quantile(0.95, sum by (le, instance) (rate(vllm:time_to_first_token_seconds_bucket[1m])))
#             A few points appear only while warmup.py sends requests; NaN or gaps between runs just mean no traffic. Observed
#             on the first attempt (2026-10-04): about 20 ms falling to about 9 ms, versus 250-350 ms measured client-side, so most
#             of the client number is tunnel overhead. KV & Prefix Cache and Scheduler & Concurrency show a small bump in
#             running requests during each run and no queueing.
#   CAUTION   On the same dashboard, the Prefill / Decode / Queue / Inference time panels and ITL are NOT usable for fast requests:
#             vLLM's histograms for them start at 0.3 s (ITL: 10 ms), every warmup request lands in that first bucket, and
#             histogram_quantile then interpolates inside it (p50 = 150 ms, p95 = 285 ms; ITL p95 = 9.5 ms). Those flat rectangles
#             only mean "under 300 ms" ("under 10 ms" for ITL). TTFT has fine buckets (1/5/10/20 ms ...), so use it, or compare
#             rate(..._sum)/rate(..._count) for means.
#   idle gap  Between runs every request-rate series flattens to zero (rate over an idle counter = NaN in quantile panels).
#   Both workers and the gateway are scraped by two jobs, so each series appears twice (pod-IP label and service-name label);
#   each pod restart also creates a new pod-IP series. Filter by job when you sum (docs/inference-experiments/README.md).
#
# FILES ON DISK  (metrics/inference/$RUN_ID/, gitignored; RUN_ID comes from experiments/manifest/e1-warmup.env)
#   restart/worker-a|worker-b/restart_recovery_summary.json   old/new pod, container start, Ready time, recovery seconds, smoke result
#   cold/warmup_summary.json  (+ raw/)   per worker: unwarmed TTFT then warm p50/p95 right after the restart.  {} = nothing measured.
#   warm/warmup_summary.json  (+ raw/)   same measurement after the wait
#   prometheus_range/<query>.json        15 s time series: HBM, KV usage, running/waiting, TTFT p95, queue/pick rates (WINDOW-level)
#   kubectl/  logs/  hardware/  prometheus/   cluster snapshot from step 6/6 (pods JSON, worker + DCGM logs, nvidia-smi CSVs, .prom scrapes)
#   If the local pull fails or looks wrong, the same story is in the dashboards above for the same time window.
#
# HOW TO READ THE RESULT
#   Checkpoint: cold unwarmed TTFT visibly higher than warm p50/p95. Client-side numbers include the SSH tunnel (about 250-350 ms
#   floor), which can hide a small penalty, so confirm with the engine-side query above before concluding anything.
#
# IF IT STOPS EARLY
#   A restart that does not reach Ready within restart-test.sh's 120 s wait aborts the run. On 2026-10-04 worker A's liveness probe
#   (kill at about 120 s, no startupProbe) restarted a slow-starting container in a loop. Check: kubectl -n inference-lab get pods;
#   kubectl -n inference-lab describe pod -l app=inference-worker-a.
# =====================================================================================================================

set -euo pipefail

if [[ "${1:-}" == "--help" ]]; then
  echo "Usage: run-e1.sh   (env: WARM_WAIT_S=300 seconds between cold and warm runs, YES=1 to skip the confirmation, D=<yyyymmdd> to pin the run date)"
  echo "Restarts BOTH workers (state-changing), then runs warmup.py cold and warm through the tunnel (make inference-tunnel must be running)."
  exit 0
fi

cd "$(dirname "$0")/../../.."
# shellcheck disable=SC1091
source infra/inference/experiments/manifest/e1-warmup.env   # manifest inputs + RUN_ID
OUT="metrics/inference/$RUN_ID"
WARM_WAIT_S="${WARM_WAIT_S:-300}"

wait_ok() { # url, label: poll /health through the tunnel until 200 (port-forwards re-attach after a pod restart)
  local code=000
  for _ in $(seq 1 60); do
    code=$(curl -s -m 3 -o /dev/null -w '%{http_code}' "$1" || true)
    [[ "$code" == "200" ]] && return 0
    sleep 2
  done
  echo "ERROR: $2 did not return 200 (last code $code). Is 'make inference-tunnel' running?" >&2
  return 1
}

echo "== E1 run $RUN_ID (vLLM $VLLM_VERSION, block size $KV_BLOCK_SIZE) =="
echo "Preflight: tunnel and workers"
wait_ok http://127.0.0.1:18080/health "gateway (18080)"
wait_ok http://127.0.0.1:18001/health "worker A (18001)"
wait_ok http://127.0.0.1:18002/health "worker B (18002)"

if [[ "${YES:-}" != "1" ]]; then
  read -r -p "This restarts BOTH workers and sends test traffic. Continue as $RUN_ID? [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]] || { echo "Aborted."; exit 1; }
fi

mkdir -p "$OUT"
START_EPOCH=$(date +%s)   # start of the Prometheus range window
echo "== 1/6 restart worker B, then worker A (summaries in $OUT/restart/) =="
bash infra/inference/scripts/restart-test.sh inference-worker-b "$OUT/restart/worker-b"
bash infra/inference/scripts/restart-test.sh inference-worker-a "$OUT/restart/worker-a"

echo "== 2/6 wait for the tunnel port-forwards to re-attach to the new pods =="
wait_ok http://127.0.0.1:18001/health "worker A (18001)"
wait_ok http://127.0.0.1:18002/health "worker B (18002)"

echo "== 3/6 cold run =="
python3 infra/inference/experiments/warmup.py --output-dir "$OUT/cold"
if [[ "$(tr -d '[:space:]' < "$OUT/cold/warmup_summary.json")" == "{}" ]]; then
  echo "ERROR: cold run produced no measurement ({}); not continuing. Check the tunnel and worker health, then rerun." >&2
  exit 1
fi

echo "== 4/6 wait ${WARM_WAIT_S}s with no other traffic, then warm run =="
for ((s = WARM_WAIT_S; s > 0; s -= 30)); do echo "  ${s}s left"; sleep $((s < 30 ? s : 30)); done
python3 infra/inference/experiments/warmup.py --output-dir "$OUT/warm"

END_EPOCH=$(date +%s)
echo "== 5/6 pull Prometheus range ($START_EPOCH .. $END_EPOCH) =="
make inference-pull-range RUN_ID="$RUN_ID" START="$START_EPOCH" END="$END_EPOCH"

echo "== 6/6 pull evidence =="
bash infra/inference/scripts/pull-evidence.sh "$RUN_ID"

echo "== E1 done: $OUT (cold/ and warm/ warmup_summary.json, restart/worker-a|b, cluster snapshots) =="
