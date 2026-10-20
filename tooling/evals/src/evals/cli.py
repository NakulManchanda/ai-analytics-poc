"""`evals` command line: validate, refresh-reference, report, synth."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
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
    c = sub.add_parser(
        "candidates",
        help="propose golden-case candidates for captured runs (suggestions only)",
    )
    c.add_argument("--traces", type=Path, required=True)
    sr = sub.add_parser("session-report", help="report for one captured session")
    sr.add_argument("--traces", type=Path, required=True)
    sr.add_argument("--matches", type=Path, required=True)
    sr.add_argument(
        "--origin",
        default=None,
        help="optional cross-check only: 'live' is refused unless the capture records execution_mode=live",
    )
    sr.add_argument(
        "--provisional",
        action="store_true",
        help="grade against pending_human_review references (labelled)",
    )
    sr.add_argument(
        "--conversation-id",
        help="select one conversation from a multi-conversation file",
    )
    sr.add_argument(
        "--events-dir",
        type=Path,
        help="optional directory with run_<id>.txt SSE event logs",
    )
    sr.add_argument("--out", type=Path)
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
    if args.cmd == "candidates":
        from evals.matching import suggest_candidates
        from evals.traces import build_run_views, load_records

        views = build_run_views(load_records(args.traces)).values()
        for cand in suggest_candidates(list(views), load_cases()):
            print(json.dumps(asdict(cand)))
        return 0
    if args.cmd == "session-report":
        from evals.session import SessionError, render_session_report

        try:
            text = render_session_report(
                args.traces,
                args.matches,
                load_cases(),
                args.origin,
                args.provisional,
                args.conversation_id,
                events_dir=args.events_dir,
            )
        except SessionError as err:
            print(f"error: {err}", file=sys.stderr)
            return 2
        if args.out:
            args.out.write_text(text)
        print(text)
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
