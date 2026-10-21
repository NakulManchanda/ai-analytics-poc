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


def _cells(line: str) -> list[str]:
    return [x.strip() for x in line.strip().strip("|").split("|")]


def parse_report(text: str) -> dict:
    """Parse one session report.

    Returns ``{"turns": {(case_id, turn_index): {...}}, "cost": float}`` where each turn has
    execution/tool/answer/actual/expected/reasons/question/answer_text.
    """
    turns: dict[tuple[str, int], dict] = {}
    by_num: dict[int, tuple[str, int]] = {}
    if "## Turns" in text:
        sec = text.split("## Turns", 1)[1].split("\n## ", 1)[0]
        for line in sec.splitlines():
            if not re.match(r"\|\s*\d+\s*\|", line):
                continue
            c = _cells(line)
            m = re.match(r"`?([\w-]+)#(\d+)`?", c[2]) if len(c) > 9 else None
            if not m:
                continue
            key = (m[1], int(m[2]))
            by_num[int(c[0])] = key
            turns[key] = dict(
                question=c[1],
                execution=c[3],
                tool=c[4],
                answer=c[5],
                actual=c[7],
                expected=c[8],
                reasons=c[9],
                answer_text="",
            )
        cur: int | None = None
        buf: list[str] = []

        def flush() -> None:
            if cur in by_num:
                turns[by_num[cur]]["answer_text"] = "\n".join(buf).strip()

        for line in sec.splitlines():
            m = re.match(r"- turn (\d+) answer: ?(.*)$", line)
            if m:
                flush()
                cur, buf = int(m[1]), [m[2]]
            elif cur is not None:
                buf.append(line)
        flush()
    cost = 0.0
    m = re.search(r"\| total \|[^\n]*", text)
    if m:
        try:
            cost = float(_cells(m[0])[5])
        except (ValueError, IndexError):
            pass
    return {"turns": turns, "cost": cost}


def load_run_reports(run_dir: Path) -> tuple[dict[tuple[str, int], dict], float]:
    turns: dict[tuple[str, int], dict] = {}
    cost = 0.0
    for f in sorted((run_dir / "report").glob("*.md")):
        p = parse_report(f.read_text())
        turns.update(p["turns"])
        cost += p["cost"]
    return turns, cost


def load_baseline(path: Path) -> dict[tuple[str, int], dict]:
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
    "not_graded": -1,
    "missing": -1,
}


def direction(old: str, new: str) -> str:
    return "improved" if RANK.get(new, 0) > RANK.get(old, 0) else "regressed"
