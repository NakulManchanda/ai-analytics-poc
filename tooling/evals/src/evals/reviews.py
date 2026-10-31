"""Human review verdicts for a frozen run (#190): append-only ``reviews.jsonl``.

Evidence (captures snapshot, ``results.jsonl``, grader reports) is never touched; reviews live
beside it and are bound to the snapshot digest, so a re-frozen run cannot silently reuse them.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from evals.runs import (
    MISSING,
    RunError,
    load_run_reports,
    read_jsonl,
    write_json_atomic,
)

SCHEMA_VERSION = 1
HUMAN_DIMS = ("tool", "answer", "resolution")
GRADED_DIMS = ("tool", "answer")  # dimensions the automated grader also scores
VERDICTS = ("correct", "incorrect", "needs_review")
REVIEWS_FILE = "reviews.jsonl"
RESOLVED_FILE = "reviews.resolved.json"
_REQUIRED = ("review_id", "eval_run_id", "case_id", "turn_index", "snapshot_sha256")
_REQUIRED += ("dimension", "verdict", "reviewer", "reviewed_at")


def snapshot_digest(run_dir: Path) -> str:
    """Digest recorded by ``run-freeze``, re-verified against the snapshot file."""
    snap = run_dir / "captures.snapshot.jsonl"
    rec = run_dir / "captures.snapshot.sha256"
    if not snap.is_file() or not rec.is_file():
        raise RunError(f"{run_dir.name} is not frozen; run `evals run-freeze` first")
    recorded = rec.read_text().split()[0]
    if hashlib.sha256(snap.read_bytes()).hexdigest() != recorded:
        raise RunError(
            f"{snap} no longer matches {rec.name}; frozen evidence was modified"
        )
    return recorded


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    return ts if ts.tzinfo else None


def validate(rec: object) -> str | None:
    """Reason the record is malformed, else ``None``."""
    if not isinstance(rec, dict):
        return "not a JSON object"
    if rec.get("schema_version") != SCHEMA_VERSION:
        return f"schema_version must be {SCHEMA_VERSION}"
    for k in _REQUIRED:
        if k not in rec or rec[k] in (None, ""):
            return f"missing {k}"
    if not isinstance(rec["turn_index"], int) or isinstance(rec["turn_index"], bool):
        return "turn_index must be an integer"
    if rec["dimension"] not in HUMAN_DIMS:
        return f"dimension must be one of {', '.join(HUMAN_DIMS)}"
    if rec["verdict"] not in VERDICTS:
        return f"verdict must be one of {', '.join(VERDICTS)}"
    if _parse_ts(rec["reviewed_at"]) is None:
        return "reviewed_at must be a timezone-aware ISO-8601 timestamp"
    return None


def new_record(
    run_dir: Path,
    case_id: str,
    turn_index: int,
    dimension: str,
    verdict: str,
    reviewer: str,
    *,
    failure_layer: str | None = None,
    failure_category: str | None = None,
    notes: str = "",
    now: datetime | None = None,
) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "review_id": uuid.uuid4().hex,
        "eval_run_id": run_dir.name,
        "case_id": case_id,
        "turn_index": turn_index,
        "snapshot_sha256": snapshot_digest(run_dir),
        "dimension": dimension,
        "verdict": verdict,
        "failure_layer": failure_layer,
        "failure_category": failure_category,
        "reviewer": reviewer,
        "reviewed_at": (now or datetime.now(UTC)).isoformat().replace("+00:00", "Z"),
        "notes": notes,
    }


def append_review(run_dir: Path, rec: dict) -> None:
    """Validate and append one record; the turn must exist in this run's ``results.jsonl``."""
    err = validate(rec)
    if err:
        raise RunError(f"invalid review: {err}")
    known = {
        (r["case_id"], r["turn_index"]) for r in read_jsonl(run_dir / "results.jsonl")
    }
    if (rec["case_id"], rec["turn_index"]) not in known:
        raise RunError(
            f"{rec['case_id']}#{rec['turn_index']} is not a turn of {run_dir.name}"
        )
    with (run_dir / REVIEWS_FILE).open("a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")


@dataclass
class Reviews:
    resolved: dict[tuple[str, int, str], dict] = field(default_factory=dict)
    malformed: list[tuple[int, str]] = field(
        default_factory=list
    )  # (line number, reason)
    stale: list[str] = field(default_factory=list)  # review_ids bound to other evidence
    duplicates: list[str] = field(
        default_factory=list
    )  # repeated review_ids (first wins)


def load_reviews(run_dir: Path) -> Reviews:
    """Resolve ``reviews.jsonl`` against the run's current evidence.

    Per (case, turn, dimension) the latest ``reviewed_at`` wins, file order breaking ties;
    earlier verdicts stay listed under ``superseded``. Malformed lines, repeated review ids and
    records bound to a different run or snapshot digest are reported, never applied.
    """
    out = Reviews()
    path = run_dir / REVIEWS_FILE
    if not path.is_file():
        return out
    digest = snapshot_digest(run_dir)
    seen: set[str] = set()
    groups: dict[tuple[str, int, str], list[tuple[datetime, int, dict]]] = {}
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            out.malformed.append((n, "invalid JSON"))
            continue
        err = validate(rec)
        if err:
            out.malformed.append((n, err))
        elif rec["review_id"] in seen:
            out.duplicates.append(rec["review_id"])
        elif rec["snapshot_sha256"] != digest or rec["eval_run_id"] != run_dir.name:
            seen.add(rec["review_id"])
            out.stale.append(rec["review_id"])
        else:
            seen.add(rec["review_id"])
            key = (rec["case_id"], rec["turn_index"], rec["dimension"])
            groups.setdefault(key, []).append((_parse_ts(rec["reviewed_at"]), n, rec))
    for key, items in groups.items():
        items.sort(key=lambda t: (t[0], t[1]))
        out.resolved[key] = dict(
            items[-1][2], superseded=[r["review_id"] for *_, r in items[:-1]]
        )
    return out


def disagreements(reviews: Reviews, graded: dict[tuple[str, int], dict]) -> list[dict]:
    """Turns where a human verdict differs from the automated grader's (separate, not merged)."""
    out = []
    for (case_id, turn, dim), rec in sorted(reviews.resolved.items()):
        g = graded.get((case_id, turn))
        if dim not in GRADED_DIMS or not g or g.get(dim) in MISSING:
            continue
        if g[dim] != rec["verdict"]:
            out.append(
                {"case_id": case_id, "turn_index": turn, "dimension": dim,
                 "grader": g[dim], "human": rec["verdict"], "review_id": rec["review_id"]}
            )  # fmt: skip
    return out


def materialize(run_dir: Path) -> dict:
    """Write ``reviews.resolved.json`` (the import/reporting artifact) and return it."""
    reviews = load_reviews(run_dir)
    graded, _ = load_run_reports(run_dir)
    turns = {
        (r["case_id"], r["turn_index"]) for r in read_jsonl(run_dir / "results.jsonl")
    }
    reviewed = {(c, t) for c, t, _ in reviews.resolved}
    doc = {
        "schema_version": SCHEMA_VERSION,
        "eval_run_id": run_dir.name,
        "snapshot_sha256": snapshot_digest(run_dir),
        "verdicts": [
            dict(rec, case_id=c, turn_index=t, dimension=d)
            for (c, t, d), rec in sorted(reviews.resolved.items())
        ],
        "disagreements": disagreements(reviews, graded),
        "unreviewed_turns": sorted(f"{c}#{t}" for c, t in turns - reviewed),
        "malformed": [{"line": n, "reason": r} for n, r in reviews.malformed],
        "stale": reviews.stale,
        "duplicates": reviews.duplicates,
    }
    write_json_atomic(run_dir / RESOLVED_FILE, doc)
    return doc


def load_human_verdicts(run_dir: Path) -> dict[tuple[str, int], dict[str, str]]:
    """Import ``reviews.resolved.json`` as ``{(case, turn): {dimension: verdict}}``."""
    path = run_dir / RESOLVED_FILE
    if not path.is_file():
        raise RunError(f"{path} missing; run `evals run-review-export` first")
    doc = json.loads(path.read_text())
    if doc.get("snapshot_sha256") != snapshot_digest(run_dir):
        raise RunError(f"{path.name} is bound to different evidence; re-run export")
    out: dict[tuple[str, int], dict[str, str]] = {}
    for v in doc["verdicts"]:
        out.setdefault((v["case_id"], v["turn_index"]), {})[v["dimension"]] = v[
            "verdict"
        ]
    return out
