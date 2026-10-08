#!/usr/bin/env python3
"""Explain ONE request end to end from a run directory and a pulled gateway log (#123)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.benchmarks.trace import TraceError, build_trace, load_requests, render_text


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--run-dir", required=True, type=Path, help="run_scenario.py run directory"
    )
    p.add_argument("--request-id", help="request_id from requests.jsonl (see --list)")
    p.add_argument(
        "--gateway-log",
        type=Path,
        default=None,
        help="pulled gateway log (kubectl logs deploy/inference-gateway); default "
        "<run-dir>/gateway.log if present",
    )
    p.add_argument(
        "--worker-log",
        type=Path,
        default=None,
        help="pulled worker log (kubectl logs deploy/inference-worker-b); default "
        "<run-dir>/worker.log if present",
    )
    p.add_argument(
        "--json", type=Path, default=None, help="also write the trace as JSON"
    )
    p.add_argument(
        "--list", action="store_true", help="list request ids in the run and exit"
    )
    args = p.parse_args(argv)
    try:
        if args.list:
            for r in load_requests(args.run_dir):
                print(
                    r.get("request_id"),
                    r.get("conversation_id"),
                    r.get("turn_index"),
                    r.get("status"),
                )
            return 0
        if not args.request_id:
            p.error("--request-id is required (or use --list)")
        log = args.gateway_log
        if log is None and (args.run_dir / "gateway.log").is_file():
            log = args.run_dir / "gateway.log"
        w_log = args.worker_log
        if w_log is None and (args.run_dir / "worker.log").is_file():
            w_log = args.run_dir / "worker.log"
        trace = build_trace(args.run_dir, args.request_id, log, worker_log=w_log)

    except TraceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(render_text(trace), end="")
    if args.json:
        args.json.write_text(json.dumps(trace, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
