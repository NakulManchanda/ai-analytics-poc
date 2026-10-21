"""Shared helpers for the standard eval run folder ``<root>/<run_id>/``."""

from __future__ import annotations

import json
import re
from pathlib import Path

DEFAULT_ROOT = Path("metrics/evals")
LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
TS_RE = r"\d{8}T\d{6}Z"
DIMS = ("execution", "tool", "answer")
TERMINAL_OK = "run.completed"


class RunError(ValueError):
    """User-facing run-folder problem (CLI exits 2)."""


def resolve_run(root: Path, run: str | None) -> tuple[str, Path]:
    """Return (run_id, run_dir); fall back to ``<root>/.current``."""
    if not run:
        cur = root / ".current"
        if not cur.is_file():
            raise RunError(f"no --run given and {cur} does not exist")
        run = cur.read_text().strip()
        if not run:
            raise RunError(f"{cur} is empty")
    run_dir = root / run
    if not run_dir.is_dir():
        raise RunError(f"run directory {run_dir} does not exist")
    return run, run_dir


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def load_run_reports(run_dir: Path) -> tuple[dict[tuple[str, int], dict], float]:
    """Graded turns (keyed by golden case/turn) and total cost from ``report/*.json``."""
    turns: dict[tuple[str, int], dict] = {}
    cost = 0.0
    for f in sorted((run_dir / "report").glob("*.json")):
        for r in json.loads(f.read_text())["rows"]:
            cost += r.get("cost_usd") or 0.0
            if r.get("graded") and r["case_id"] is not None:
                turns[(r["case_id"], r["turn_index"])] = dict(
                    r,
                    actual=r["actual"],
                    expected=", ".join(r["expected"]) or "none",
                )
    return turns, cost


def load_baseline(path: Path) -> dict[tuple[str, int], dict]:
    if not Path(path).is_file():
        raise RunError(f"baseline results file not found: {path}")
    return {
        (r["case_id"], r["turn_index"]): dict(
            execution=r["execution_v2"], tool=r["tool_v2"], answer=r["answer_v2"]
        )
        for r in read_jsonl(path)
    }


def merge_failed(new: dict, results: list[dict]) -> dict:
    """Add rows for turns with no graded row (non-completed terminal => failed/not_graded)."""
    out = dict(new)
    for r in results:
        k = (r["case_id"], r["turn_index"])
        if k in out:
            continue
        failed = r["terminal"] != TERMINAL_OK
        out[k] = dict(
            question=r.get("question", ""),
            graded=False,
            execution="failed" if failed else "missing",
            tool="not_graded",
            answer="not_graded",
            actual="-",
            expected="",
            reasons=r["terminal"],
            answer_text="",
        )
    return out


RANK = {
    "correct": 2,
    "needs_review": 1,
    "not_applicable": 1,
    "incorrect": 0,
    "failed": -1,
}
MISSING = {"-", "", "not_graded", "missing", None}
DIRECTIONS = ("improved", "regressed", "lateral", "unchanged", "missing_evidence")


def direction(old: str | None, new: str | None) -> str:
    if old in MISSING or new in MISSING:
        return "missing_evidence"
    if old == new:
        return "unchanged"
    a, b = RANK.get(old, 0), RANK.get(new, 0)
    return "lateral" if a == b else "improved" if b > a else "regressed"


def read_state(run_dir: Path) -> dict | None:
    p = run_dir / "state.json"
    return json.loads(p.read_text()) if p.is_file() else None


def write_json_atomic(path: Path, obj: object) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1))
    tmp.replace(path)


def require_complete(run_dir: Path, allow_incomplete: bool) -> None:
    if allow_incomplete:
        return
    st = read_state(run_dir)
    status = st["status"] if st else "missing"
    if status != "complete":
        raise RunError(
            f"{run_dir.name} state is {status!r}, not 'complete' (run-live must finish "
            "with complete evidence); pass --allow-incomplete to override"
        )
