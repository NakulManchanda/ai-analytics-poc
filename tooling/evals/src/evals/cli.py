"""`evals` command line: validate, refresh-reference, report, synth."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evals.dataset import DEFAULT_DATASET, load_cases, refresh_reference, write_cases
from evals.report import compare, regressions, render_markdown, score_run


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="evals")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate", help="validate the golden dataset")
    r = sub.add_parser(
        "refresh-reference",
        help="recompute fact values from reference SQL (needs duckdb)",
    )
    r.add_argument("--data-dir", type=Path, required=True)
    rep = sub.add_parser("report", help="baseline vs candidate delta report")
    rep.add_argument("--baseline", type=Path, required=True)
    rep.add_argument("--candidate", type=Path, required=True)
    rep.add_argument("--out", type=Path)
    s = sub.add_parser("synth", help="regenerate synthetic fixtures")
    s.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)

    if args.cmd == "validate":
        cases = load_cases()
        print(f"ok: {len(cases)} cases")
        return 0
    if args.cmd == "refresh-reference":
        cases = load_cases()
        n = refresh_reference(
            cases,
            args.data_dir / "yellow_tripdata_2024-01.parquet",
            args.data_dir / "taxi_zone_lookup.csv",
        )
        write_cases(cases, DEFAULT_DATASET)
        print(f"refreshed {n} facts")
        return 0
    if args.cmd == "synth":
        from evals.synth import generate

        generate(args.out)
        return 0
    base, cand = score_run(args.baseline), score_run(args.candidate)
    deltas = compare(base, cand)
    text = render_markdown(base, cand, deltas)
    if args.out:
        args.out.write_text(text)
    print(text)
    return 1 if regressions(deltas) else 0


if __name__ == "__main__":
    sys.exit(main())
