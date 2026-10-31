"""CLI wiring for the run-folder subcommands (run-start, run-live, run-freeze, ...)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from evals.reviews import HUMAN_DIMS, VERDICTS
from evals.runs import (
    DEFAULT_ROOT,
    LABEL_RE,
    TS_RE,
    RunError,
    require_complete,
    resolve_run,
    write_json_atomic,
)

DATASET = Path("tooling/evals/datasets/golden/v1/cases.jsonl")


def add_parsers(sub) -> None:
    def common(p, run=True):
        p.add_argument("--root", type=Path, default=DEFAULT_ROOT)
        if run:
            p.add_argument("--run", help="run_id (default: <root>/.current)")
            if p.prog.split()[-1] != "run-live":
                p.add_argument(
                    "--allow-incomplete",
                    action="store_true",
                    help="proceed although state.json is not 'complete'",
                )
        return p

    p = common(sub.add_parser("run-start", help="create a labelled run folder"), False)
    p.add_argument("--label", required=True)
    p.add_argument("--web")
    p.add_argument("--cases", nargs="*", default=[])
    p = common(sub.add_parser("run-live", help="drive golden cases through the app"))
    p.add_argument("--all", action="store_true")
    p.add_argument("--smoke", action="store_true")
    p.add_argument("--web")
    p.add_argument("--timeout", type=int, default=120)
    p.add_argument("--resume", action="store_true", help="re-run incomplete cases")
    p.add_argument("case_ids", nargs="*")
    p = common(sub.add_parser("run-freeze", help="snapshot this run's captures"))
    p.add_argument("--capture-dir", type=Path)
    p = common(sub.add_parser("run-grade", help="per-conversation provisional reports"))
    p = common(sub.add_parser("run-compare", help="compare with a frozen baseline"))
    p.add_argument("--baseline-results", type=Path, required=True)
    p.add_argument("--focus", help="comma list of case ids; trailing * = prefix")
    p = common(sub.add_parser("run-review-add", help="append one human verdict (#190)"))
    p.add_argument("--case", required=True)
    p.add_argument("--turn", type=int, required=True)
    p.add_argument("--dimension", required=True, choices=HUMAN_DIMS)
    p.add_argument("--verdict", required=True, choices=VERDICTS)
    p.add_argument("--reviewer", required=True)
    p.add_argument("--failure-layer")
    p.add_argument("--failure-category")
    p.add_argument("--notes", default="")
    common(
        sub.add_parser(
            "run-review-export", help="resolve reviews.jsonl -> reviews.resolved.json"
        )
    )
    p = common(sub.add_parser("run-review", help="write static review.html"))
    p.add_argument("--baseline-results", type=Path)


def _git_head() -> str | None:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        )
        return r.stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def _checked(a):
    run_id, run_dir = resolve_run(a.root, a.run)
    require_complete(run_dir, a.allow_incomplete)
    return run_id, run_dir


def run_start(a) -> int:
    if not LABEL_RE.match(a.label):
        raise RunError(f"invalid label {a.label!r}; must match {LABEL_RE.pattern}")
    used = re.compile(rf"^{re.escape(a.label)}(-{TS_RE})?$")
    if a.root.is_dir():
        clash = sorted(p.name for p in a.root.iterdir() if used.match(p.name))
        if clash:
            raise RunError(
                f"label {a.label!r} already used by {clash[0]}; pick a new label"
            )
    now = datetime.now(UTC)
    run_id = f"{a.label}-{now.strftime('%Y%m%dT%H%M%SZ')}"
    run_dir = a.root / run_id
    (run_dir / "logs").mkdir(parents=True)  # raises if the run dir already exists
    manifest = {
        "label": a.label,
        "run_id": run_id,
        "started_at": now.isoformat(),
        "git_head": _git_head(),
        "dataset_sha256": (
            hashlib.sha256(DATASET.read_bytes()).hexdigest()
            if DATASET.is_file()
            else None
        ),
        "web": a.web,
        "cases": a.cases,
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    (a.root / ".current").write_text(run_id)
    print(run_id)
    return 0


def run_live_cmd(a) -> int:
    from evals.live_run import run_live

    run_id, run_dir = resolve_run(a.root, a.run)
    out, incomplete = run_live(
        run_dir,
        run_id,
        a.web,
        a.case_ids,
        a.all,
        a.smoke,
        a.timeout,
        resume=a.resume,
    )
    print(f"wrote {out}")
    return 3 if incomplete else 0


def _jsonl_run_ids(path: Path) -> list[str]:
    if not path.is_file():
        return []
    rows = map(json.loads, filter(str.strip, path.read_text().splitlines()))
    return [r["run_id"] for r in rows if r.get("run_id")]


def run_freeze(a) -> int:
    run_id, run_dir = _checked(a)
    snap = run_dir / "captures.snapshot.jsonl"
    if snap.exists():
        raise RunError(f"{snap} already exists; snapshots are immutable")
    cap = a.capture_dir or a.root / "_capture"
    allowed = set(_jsonl_run_ids(run_dir / "results.jsonl"))
    orphans = sorted(set(_jsonl_run_ids(run_dir / "submitted.jsonl")) - allowed)
    kept, bad, dropped = [], 0, 0
    for f in sorted(cap.glob("*/captures.jsonl")):
        for line in f.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            if isinstance(rec, dict) and rec.get("eval_run_id") == run_id:
                if rec.get("run_id") in allowed:  # allowlist: checkpointed turns only
                    kept.append(line)
                else:  # superseded or never checkpointed
                    dropped += 1
    if not kept:
        raise RunError(
            f"no capture lines for {run_id!r} with a recorded run_id under {cap} "
            f"(dropped {dropped}, malformed {bad})"
        )
    data = ("\n".join(kept) + "\n").encode()
    tmp = snap.with_name(snap.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, snap)
    (run_dir / "captures.snapshot.sha256").write_text(
        f"{hashlib.sha256(data).hexdigest()}  {snap.name}\n"
    )
    print(
        f"froze: kept {len(kept)}, dropped {dropped} (not in results.jsonl: superseded or "
        f"unrecorded), malformed {bad} -> {snap}"
    )
    print(f"orphan run_ids (submitted, not in results): {', '.join(orphans) or 'none'}")
    return 0


def run_grade(a) -> int:
    from evals.dataset import load_cases
    from evals.session import SessionError, render_session_report

    _, run_dir = _checked(a)
    snap = run_dir / "captures.snapshot.jsonl"
    if not snap.is_file():
        raise RunError(f"{snap} missing; run `evals run-freeze` first")
    convs = sorted(
        {
            c
            for line in snap.read_text().splitlines()
            if line.strip()
            for c in [json.loads(line).get("conversation_id")]
            if c
        }
    )
    cases = load_cases()
    (run_dir / "report").mkdir(exist_ok=True)
    for conv in convs:
        rows: list[dict] = []
        try:
            text = render_session_report(
                snap,
                run_dir / "matches.json",
                cases,
                None,
                True,
                conv,
                events_dir=run_dir / "events",
                rows=rows,
            )
        except SessionError as err:
            raise RunError(f"{conv}: {err}") from err
        (run_dir / "report" / f"{conv}.md").write_text(text)
        write_json_atomic(
            run_dir / "report" / f"{conv}.json", {"conversation_id": conv, "rows": rows}
        )
    print(f"wrote {len(convs)} reports")
    return 0


def run_compare_cmd(a) -> int:
    from evals.run_compare import DEFAULT_FOCUS, render_compare

    _, run_dir = _checked(a)
    focus = tuple(a.focus.split(",")) if a.focus else DEFAULT_FOCUS
    text = render_compare(run_dir, a.baseline_results, focus)
    (run_dir / "compare.md").write_text(text)
    print(text)
    return 0


def run_review_cmd(a) -> int:
    from evals.run_review import render_review

    _, run_dir = _checked(a)
    out = run_dir / "review.html"
    out.write_text(render_review(run_dir, a.baseline_results))
    print(out)
    return 0


def run_review_add(a) -> int:
    from evals.reviews import append_review, new_record

    _, run_dir = _checked(a)
    rec = new_record(
        run_dir, a.case, a.turn, a.dimension, a.verdict, a.reviewer,
        failure_layer=a.failure_layer, failure_category=a.failure_category, notes=a.notes,
    )  # fmt: skip
    append_review(run_dir, rec)
    print(rec["review_id"])
    return 0


def run_review_export(a) -> int:
    from evals.reviews import materialize

    _, run_dir = _checked(a)
    doc = materialize(run_dir)
    print(
        f"{len(doc['verdicts'])} verdicts, {len(doc['disagreements'])} grader disagreements, "
        f"{len(doc['coverage']['untouched'])} untouched, {len(doc['coverage']['partial'])} partial, "
        f"{doc['coverage']['complete']} complete turns; {len(doc['malformed'])} malformed, "
        f"{len(doc['stale'])} stale, {len(doc['duplicates'])} duplicate, {len(doc['orphaned'])} orphaned -> {run_dir / 'reviews.resolved.json'}"
    )
    return 0


HANDLERS = {
    "run-start": run_start,
    "run-live": run_live_cmd,
    "run-freeze": run_freeze,
    "run-grade": run_grade,
    "run-compare": run_compare_cmd,
    "run-review": run_review_cmd,
    "run-review-add": run_review_add,
    "run-review-export": run_review_export,
}


def dispatch(a) -> int:
    try:
        return HANDLERS[a.cmd](a)
    except (RunError, FileExistsError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
