"""Report for one captured multi-turn session: correctness, tokens/cost, tools, latency.

Origin is derived from recorded provenance so scripted/local results are never mistaken for live provider results.
"""

from __future__ import annotations

import json
from pathlib import Path

from evals.graders import grade_turn
from evals.matching import load_matches, resolve
from evals.schema import GoldenCase
from evals.traces import TurnView, build_run_views, load_records

LOCAL = "The model provider and dataset server were not verified live (scripted fixtures or unrecorded provenance); tokens, cost, latency and answers are not live Bedrock measurements. No live Bedrock run backs these numbers."


class SessionError(ValueError):
    pass


def _n(value: float | None, spec: str = "g") -> str:
    return "n/a" if value is None else format(value, spec)


def _tools(view: TurnView) -> str:
    return (
        "; ".join(
            f"{t.name}({json.dumps(t.arguments, sort_keys=True)}) {t.status or '?'} rows={_n(t.row_count)}"
            for t in view.tools
        )
        or "none"
    )


def render_session_report(
    traces: Path,
    matches: Path,
    cases: list[GoldenCase],
    claimed_origin: str | None = None,
    provisional: bool = False,
    conversation_id: str | None = None,
) -> str:
    """Provenance comes from the capture itself (``execution_mode`` written by the recorder).

    ``claimed_origin`` is only a cross-check: claiming ``live`` for a capture that is not recorded
    as live is refused. LIVE requires every record to say ``live``; missing means NOT LIVE.
    """
    records = load_records(traces)
    if conversation_id is not None:
        records = [r for r in records if r.conversation_id == conversation_id]
        if not records:
            raise SessionError(f"no records for conversation {conversation_id}")
    conversations = sorted({r.conversation_id or "(none)" for r in records})
    if len(conversations) != 1:
        raise SessionError(
            f"{traces.name} holds {len(conversations)} conversations ({', '.join(conversations)}); "
            "pass --conversation-id to select exactly one"
        )
    modes = sorted({r.execution_mode or "unverified" for r in records})
    live = modes == ["live"]
    if claimed_origin == "live" and not live:
        raise SessionError(
            f"--origin live refused: capture records execution_mode={','.join(modes)}"
        )
    origin = (
        "live" if live else f"NOT LIVE (recorded execution_mode: {', '.join(modes)})"
    )
    views = build_run_views(records)
    res = resolve(views, load_matches(matches, cases), cases, provisional)
    ordered = sorted(views.values(), key=lambda v: v.started_at or "")
    by_id = {c.id: c for c in cases}
    link: dict[str, tuple[str, int]] = {v.run_id: k for k, v in res.matched.items()} | {
        v.run_id: (m.case_id, m.turn_index) for v, m in res.unreviewed
    }

    out = [
        "# Captured session report",
        "",
        f"- origin: **{'LIVE' if live else origin}** (derived from recorded provenance, not a CLI flag)"
        + ("" if live else f" - {LOCAL}"),
        f"- source: `{traces.name}` (capture.v1), runs: {len(ordered)}, conversations: {len({v.conversation_id for v in ordered})}",
        "- captured traffic is never golden; a graded comparison needs an explicit match to a human-reviewed golden case.",
    ]
    if res.provisional:
        out.append(
            "- **PROVISIONAL**: graded against golden references that are still `pending_human_review`. Correctness below is not an approved result."
        )
    out += [
        "",
        "## Turns",
        "",
        "| # | question | linked golden turn | grading | tools (actual) | tools expected |",
        "|---|---|---|---|---|---|",
    ]
    notes: list[str] = []
    for i, v in enumerate(ordered):
        target = link.get(v.run_id)
        if target is None:
            state, golden, expected = "candidate only (no confirmed match)", "-", "-"
        else:
            case = by_id[target[0]]
            turn = case.turns[target[1]]
            golden = f"`{target[0]}#{target[1]}`"
            expected = ", ".join(e.name for e in turn.expected_tools) or "none"
            if target in res.matched:
                g = grade_turn(target[0], target[1], turn, v)
                checks = ", ".join(
                    f"{k}={'pass' if ok else 'FAIL'}" for k, ok in g.checks.items()
                )
                state = f"{'provisional ' if res.provisional else ''}{'PASS' if g.passed else 'FAIL'}: {checks}"
            else:
                state = "candidate only (golden case pending_human_review; not graded)"
        out.append(
            f"| {i} | {v.question or '(content not captured)'} | {golden} | {state} | {_tools(v)} | {expected} |"
        )
        if v.answer:
            notes.append(f"- turn {i} answer: {v.answer}")
    out += [
        "",
        *notes,
        "",
        "## Tokens, cost, latency",
        "",
        "| # | model calls | aliases | input | output | cost USD | model ms | tool ms |",
        "|---|---|---|---|---|---|---|---|",
    ]
    incomplete = False
    totals = {"in": 0, "out": 0, "cost": 0.0, "model": 0.0, "tool": 0.0}
    for i, v in enumerate(ordered):
        calls = v.model_calls
        incomplete |= any(
            c.input_tokens is None or c.output_tokens is None or c.cost_usd is None
            for c in calls
        )
        aliases = sorted({c.model_alias or "?" for c in calls})
        tool_ms = sum(t.latency_ms for t in v.tools if t.latency_ms is not None)
        model_ms = v.latency_ms
        out.append(
            f"| {i} | {len(calls)} | {', '.join(aliases) or 'catalogue (no model call)'} | {_n(v.input_tokens)} | {_n(v.output_tokens)} | {_n(v.cost_usd, '.6f')} | {_n(model_ms, '.0f')} | {_n(tool_ms, '.0f')} |"
        )
        totals["in"] += v.input_tokens or 0
        totals["out"] += v.output_tokens or 0
        totals["cost"] += v.cost_usd or 0.0
        totals["model"] += model_ms or 0.0
        totals["tool"] += tool_ms
    suffix = (
        " (lower bound: some calls had unknown usage or cost - incomplete)"
        if incomplete
        else ""
    )
    out.append(
        f"| total | | | {totals['in']} | {totals['out']} | {totals['cost']:.6f} | {totals['model']:.0f} | {totals['tool']:.0f} |"
    )
    calls_all = [c for v in ordered for c in v.model_calls]
    versions = sorted({c.pricing_version for c in calls_all if c.pricing_version})
    out += [
        "",
        f"Totals are sums of known values only{suffix}. `n/a` means the provider reported no usage; it is never counted as 0.",
        f"Pricing versions: {', '.join(versions) or 'none'}; cost kinds: {', '.join(sorted({c.cost_kind or 'unknown' for c in calls_all}))}; fallbacks used: {sum(bool(c.fallback_used) for c in calls_all)}.",
        "Judge metrics: not run on captured traffic (no recorded judge verdicts exist for it).",
        "",
    ]
    return "\n".join(out)
