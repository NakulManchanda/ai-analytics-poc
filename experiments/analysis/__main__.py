"""`python -m experiments.analysis RUN_DIR [RUN_DIR ...] [--range DIR]`: quick text summary."""

from __future__ import annotations

import argparse
from pathlib import Path

from experiments.analysis import breakdown, load_run, memory_proof, sweep_curve
from experiments.analysis.display import show


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "runs", nargs="*", type=Path, help="run directories (contain requests.jsonl)"
    )
    ap.add_argument(
        "--range", type=Path, help="prometheus_range directory (memory proof)"
    )
    args = ap.parse_args()
    for path in args.runs:
        run = load_run(path)
        print(f"#### {path} ({run.label}); load warnings: {run.warnings or 'none'}")
        if run.sweep:
            show("offered load vs throughput vs goodput", sweep_curve(run))
        if run.requests and len(run.levels) <= 1:
            for by in ("workload_class", "tenant", "worker"):
                show(f"latency by {by}", breakdown(run, by))
    if args.range:
        res = memory_proof(args.range)
        res.pop("series"), res.pop("timestamps")
        show("memory proof", res)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
