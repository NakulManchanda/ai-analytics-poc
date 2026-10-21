#!/usr/bin/env bash
# Run a command, tee its stdout+stderr, and keep the log with the current eval run (issue #200).
# usage: scripts/eval-log.sh <target-name> -- <command> [args...]
# The log lands in <EVAL_ROOT>/<run_id>/logs/<target>-<UTC ts>.log (+ <target>.latest.log). The run is read
# from <EVAL_ROOT>/.current AFTER the command finishes, so the command that creates the run is logged too.
# The command's exit status is preserved.
set -uo pipefail
name="$1"; shift; [ "${1:-}" = "--" ] && shift
root="${EVAL_ROOT:-metrics/evals}"
ts="$(date -u +%Y%m%dT%H%M%SZ)"
tmp="$(mktemp)"
{ echo "# $name $(date -u +%FT%TZ)"; "$@"; } 2>&1 | tee "$tmp"
status="${PIPESTATUS[0]}"
run="$(cat "$root/.current" 2>/dev/null || true)"
if [ -n "$run" ] && [ -d "$root/$run" ]; then dir="$root/$run/logs"; else dir="$root/_logs"; fi
mkdir -p "$dir"
mv "$tmp" "$dir/$name-$ts.log"
ln -sf "$name-$ts.log" "$dir/$name.latest.log"
echo "log: $dir/$name-$ts.log" >&2
exit "$status"
