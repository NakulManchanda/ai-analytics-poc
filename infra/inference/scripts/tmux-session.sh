#!/usr/bin/env bash
# tmux session for an inference experiment sitting: start with `make inference-tmux-start`, finish with `make inference-tmux-end`.
#   start: session "inference" with windows  run (your shell at the repo root: run make inference-e1 etc.),
#          tunnel (make inference-tunnel, left running), watch (live pods on the instance). Attaches when run from a terminal.
#   end:   kills the session (and with it the local tunnel) and the tunnel's helper loops left running on the instance.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

SESSION="${TMUX_SESSION:-inference}"
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"

usage() { echo "Usage: tmux-session.sh start|end   (env: TMUX_SESSION=inference, NO_ATTACH=1 to create without attaching)"; }

case "${1:-}" in
  --help | -h) usage; exit 0 ;;
  start)
    command -v tmux >/dev/null || { echo "ERROR: tmux is not installed" >&2; exit 1; }
    load_local_env; require_connection
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      echo "Session '$SESSION' already exists."
    else
      tmux new-session -d -s "$SESSION" -n run -c "$ROOT"
      tmux send-keys -t "$SESSION:run" "echo 'Ready. Tunnel is in window 1, pods in window 2. Run: make inference-e1'" Enter
      tmux new-window -t "$SESSION" -n tunnel -c "$ROOT"
      tmux send-keys -t "$SESSION:tunnel" "make inference-tunnel" Enter
      tmux new-window -t "$SESSION" -n watch -c "$ROOT"
      tmux send-keys -t "$SESSION:watch" \
        "ssh -i '$LAMBDA_SSH_KEY_PATH' '$(ssh_target)' 'sudo k3s kubectl -n ${INFERENCE_NAMESPACE:-inference-lab} get pods -w'" Enter
      tmux select-window -t "$SESSION:run"
      echo "Created session '$SESSION' (windows: run, tunnel, watch)."
    fi
    if [[ "${NO_ATTACH:-}" == "1" || ! -t 1 ]]; then
      echo "Attach with: tmux attach -t $SESSION"
    elif [[ -n "${TMUX:-}" ]]; then
      tmux switch-client -t "$SESSION"
    else
      exec tmux attach -t "$SESSION"
    fi
    ;;
  end)
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      tmux kill-session -t "$SESSION"
      echo "Killed tmux session '$SESSION' (local tunnel stopped)."
    else
      echo "No tmux session '$SESSION'."
    fi
    load_local_env; require_connection
    # The tunnel starts `while true; kubectl port-forward` loops on the instance; they can outlive the ssh connection.
    # The [k] keeps pkill from matching its own command line.
    ssh_cmd "pkill -f '[k]ubectl.*port-forward' || true; echo 'Stopped leftover port-forward loops on the instance.'"
    ;;
  *) usage; exit 2 ;;
esac
