"""Markdown summary of a run's human review (#190): coverage, agreement with the grader, queue.

Grader outcomes and human verdicts are reported side by side and never merged.
"""

from __future__ import annotations

import collections
from datetime import UTC, datetime
from pathlib import Path

from evals.reviews import (
    GRADED_DIMS,
    HUMAN_DIMS,
    VERDICTS,
    coverage,
    disagreements,
    load_reviews,
    required_dims,
    reviews_digest,
    snapshot_digest,
)
from evals.runs import (
    DIMS,
    MISSING,
    TERMINAL_OK,
    load_run_reports,
    merge_failed,
    read_jsonl,
)

QUEUE_LIMIT = 25


def _md(s: object) -> str:
    return str(s or "").replace("|", "\\|").replace("\n", " ")


def summary_lines(run_dir: Path) -> list[str]:
    """The "Human review" section; empty when the run has no reviews file."""
    if not (run_dir / "reviews.jsonl").is_file():
        return []
    reviews = load_reviews(run_dir)
    graded, _ = load_run_reports(run_dir)
    results = read_jsonl(run_dir / "results.jsonl")
    turns = {(r["case_id"], r["turn_index"]) for r in results}
    cov = coverage(turns, reviews)
    L = ["## Human review (separate from grader outcomes)", ""]
    L.append(
        f"turns: {len(turns)}; annotated {cov['complete']} (partial {len(cov['partial'])}, "
        f"untouched {len(cov['untouched'])}); adjudicated {cov['adjudicated']} "
        f"(annotated with no human needs_review); unresolved needs_review "
        f"{len(cov['needs_review'])} (required: {cov['required']})"
    )
    L.append(
        f"records not applied: malformed {len(reviews.malformed)}, stale {len(reviews.stale)}, "
        f"duplicate {len(reviews.duplicates)}, orphaned {len(reviews.orphaned)}"
    )
    L += [
        "",
        "| dimension | " + " | ".join(VERDICTS) + " | unreviewed |",
        "|---|---|---|---|---|",
    ]
    for dim in HUMAN_DIMS:
        counts = collections.Counter(
            r["verdict"] for (c, t, d), r in reviews.resolved.items() if d == dim
        )
        applicable = {k for k in turns if dim in required_dims(k[1])}
        reviewed = sum(
            1 for (c, t, d) in reviews.resolved if d == dim and (c, t) in applicable
        )
        extra = sum(counts.values()) - reviewed
        L.append(
            f"| {dim} | "
            + " | ".join(str(counts.get(v, 0)) for v in VERDICTS)
            + f" | {len(applicable) - reviewed}"
            + (f" (+{extra} optional) |" if extra else " |")
        )
    both = collections.Counter()
    for (c, t, d), rec in reviews.resolved.items():
        g = graded.get((c, t))
        if d in GRADED_DIMS and g and g.get(d) not in MISSING:
            both[(d, g[d] == rec["verdict"])] += 1
    L += ["", "### Grader vs human", ""]
    for dim in GRADED_DIMS:
        agree, differ = both.get((dim, True), 0), both.get((dim, False), 0)
        L.append(
            f"- {dim}: {agree} agree, {differ} differ (turns with both a grader row and a verdict)"
        )
    diffs = disagreements(reviews, graded)
    if diffs:
        L += [
            "",
            "| turn | dimension | grader | human | layer / category | notes |",
            "|---|---|---|---|---|---|",
        ]
        for x in diffs:
            rec = reviews.resolved[(x["case_id"], x["turn_index"], x["dimension"])]
            tag = " / ".join(
                v for v in (rec.get("failure_layer"), rec.get("failure_category")) if v
            )
            L.append(
                f"| {x['case_id']}#{x['turn_index']} | {x['dimension']} | {x['grader']} | "
                f"{x['human']} | {_md(tag)} | {_md(rec.get('notes'))[:120]} |"
            )
    return L


def queue(run_dir: Path) -> list[tuple[str, str]]:
    """Turns still needing a verdict, most informative first: (turn, why)."""
    reviews = load_reviews(run_dir) if (run_dir / "reviews.jsonl").is_file() else None
    graded, _ = load_run_reports(run_dir)
    results = read_jsonl(run_dir / "results.jsonl")
    merged = merge_failed(graded, results)
    have = collections.defaultdict(set)
    for c, t, d in reviews.resolved if reviews else ():
        have[(c, t)].add(d)
    out = []
    for r in sorted(results, key=lambda r: (r["case_id"], r["turn_index"])):
        k = (r["case_id"], r["turn_index"])
        if set(required_dims(k[1])) <= have[k]:
            continue
        g = merged[k]
        if r["terminal"] != TERMINAL_OK:
            rank, why = 0, f"run did not complete ({r['terminal']})"
        elif any(g[d] == "needs_review" for d in DIMS):
            rank, why = 1, "grader says needs_review"
        elif any(g[d] == "incorrect" for d in DIMS):
            rank, why = 2, "grader marked a dimension incorrect"
        else:
            rank, why = 3, "grader marked all correct (spot check)"
        out.append((rank, k, why))
    out.sort()
    return [(f"{k[0]}#{k[1]}", why) for _, k, why in out]


def render_review_report(run_dir: Path, now: datetime | None = None) -> str:
    L = [f"# Human review report: {run_dir.name}", ""]
    stamp = (
        (now or datetime.now(UTC)).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
    L.append(
        f"generated {stamp} from snapshot {snapshot_digest(run_dir)[:12]} and "
        f"reviews {reviews_digest(run_dir)[:12]}; this file does not update itself: re-run "
        "`make eval-review-report` after new verdicts and compare the reviews digest."
    )
    L.append("")
    body = summary_lines(run_dir)
    L += body or ["No reviews recorded yet (`make eval-review-serve`)."]
    nr = coverage(
        {
            (r["case_id"], r["turn_index"])
            for r in read_jsonl(run_dir / "results.jsonl")
        },
        load_reviews(run_dir),
    )["needs_review"]
    L += ["", f"## Unresolved human needs_review ({len(nr)} turns)", ""]
    L += [f"- {t}" for t in nr] or ["- none"]
    todo = queue(run_dir)
    L += ["", f"## Still to review ({len(todo)} turns, most informative first)", ""]
    for turn, why in todo[:QUEUE_LIMIT]:
        L.append(f"- {turn}: {why}")
    if len(todo) > QUEUE_LIMIT:
        L.append(
            f'- ... and {len(todo) - QUEUE_LIMIT} more (filter "incomplete review" on the page)'
        )
    if not todo:
        L.append("- none: every turn has the required verdicts")
    return "\n".join(L) + "\n"
