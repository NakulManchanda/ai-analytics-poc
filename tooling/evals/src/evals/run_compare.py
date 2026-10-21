"""Compare a run's per-conversation reports with a frozen baseline rollup."""

from __future__ import annotations

import collections
from pathlib import Path

from evals.runs import (
    DIMS,
    TERMINAL_OK,
    direction,
    load_baseline,
    load_run_reports,
    merge_failed,
    read_jsonl,
)

DEFAULT_FOCUS = ("det-006", "det-007", "det-013", "react-001", "uns-*")


def _is_focus(case_id: str, focus: tuple[str, ...]) -> bool:
    return any(
        case_id.startswith(f[:-1]) if f.endswith("*") else case_id == f for f in focus
    )


def changes(new: dict, old: dict) -> dict[tuple[str, int, str], str]:
    """(case, turn, dim) -> improved|regressed for every differing dimension."""
    out = {}
    for k in sorted(set(new) | set(old)):
        for dim in DIMS:
            a, b = old.get(k, {}).get(dim, "-"), new.get(k, {}).get(dim, "-")
            if a != b:
                out[(k[0], k[1], dim)] = direction(a, b)
    return out


def render_compare(
    run_dir: Path, baseline: Path, focus: tuple[str, ...] = DEFAULT_FOCUS
) -> str:
    graded, cost = load_run_reports(run_dir)
    results = read_jsonl(run_dir / "results.jsonl")
    new = merge_failed(graded, results)
    old = load_baseline(baseline)
    keys = sorted(set(new) | set(old))
    L = [f"# {run_dir.name} vs frozen baseline (provisional grader v2)", ""]
    L.append(
        f"turns: new {len(new)}, frozen {len(old)}; capture cost of graded turns: ${cost:.6f}"
    )
    L += [
        "",
        "## Outcome counts",
        "",
        "| dimension | outcome | frozen | this run |",
        "|---|---|---|---|",
    ]
    for dim in DIMS:
        cn = collections.Counter(v[dim] for v in new.values())
        co = collections.Counter(v[dim] for v in old.values())
        for o in sorted(set(cn) | set(co)):
            L.append(f"| {dim} | {o} | {co.get(o, 0)} | {cn.get(o, 0)} |")
    ch = changes(new, old)
    L += [
        "",
        "## Per-turn changes",
        "",
        "| turn | dim | frozen | new | direction |",
        "|---|---|---|---|---|",
    ]
    for (c, t, dim), d in ch.items():
        L.append(
            f"| {c}#{t} | {dim} | {old.get((c, t), {}).get(dim, '-')} | "
            f"{new.get((c, t), {}).get(dim, '-')} | {d} |"
        )
    vals = list(ch.values())
    L += [
        "",
        f"improved: {vals.count('improved')}, regressed: {vals.count('regressed')}",
    ]
    L += ["", "## Failed runs (no graded row)", ""]
    for r in sorted(results, key=lambda r: (r["case_id"], r["turn_index"])):
        if r["terminal"] != TERMINAL_OK:
            L.append(
                f"- {r['case_id']}#{r['turn_index']} {r.get('run_id')} {r['terminal']} - "
                f"{r.get('question', '')[:70]}"
            )
    L += [
        "",
        "## Focus cases",
        "",
        "| turn | execution | tool | answer | actual tools | expected |",
        "|---|---|---|---|---|---|",
    ]
    for k in keys:
        if _is_focus(k[0], focus):
            n, o = new.get(k, {}), old.get(k, {})
            L.append(
                f"| {k[0]}#{k[1]} | "
                + " | ".join(f"{o.get(d, '-')}→{n.get(d, '-')}" for d in DIMS)
                + f" | {n.get('actual', '')[:90]} | {n.get('expected', '')} |"
            )
    return "\n".join(L) + "\n"
