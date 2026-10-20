"""Report for one captured multi-turn session: correctness, tokens/cost, tools, latency.

Origin is derived from recorded provenance so scripted/local results are never mistaken for live provider results.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from evals.graders import (
    FAULT_INJECTION_TAG,
    Outcome,
    TurnOutcome,
    grade_turn,
    grade_turn_v2,
)
from evals.matching import load_matches, resolve
from evals.schema import GoldenCase, Turn
from evals.traces import (
    TurnView,
    attach_outputs,
    build_run_views,
    load_records,
    load_tool_outputs,
)

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


def _basis_tag(tags: tuple[str, ...] | list[str]) -> str:
    for t in tags:
        if t.startswith("basis:"):
            return t.split(":", 1)[1]
        if t.startswith("basis-"):
            return t
        if t == "mixed":
            return "mixed"
    return "unspecified"


def render_session_report(
    traces: Path,
    matches: Path,
    cases: list[GoldenCase],
    claimed_origin: str | None = None,
    provisional: bool = False,
    conversation_id: str | None = None,
    events_dir: Path | None = None,
) -> str:
    """Provenance comes from the capture itself (``execution_mode`` written by the recorder).

    ``claimed_origin`` is only a cross-check: claiming ``live`` for a capture that is not recorded
    as live is refused. LIVE requires every record to say ``live``; missing means NOT LIVE.
    """
    records = load_records(traces)
    if conversation_id is not None and conversation_id != "all":
        records = [r for r in records if r.conversation_id == conversation_id]
        if not records:
            raise SessionError(f"no records for conversation {conversation_id}")
    conversations = sorted({r.conversation_id or "(none)" for r in records})
    if len(conversations) != 1 and conversation_id != "all":
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
    if events_dir is not None:
        outputs_by_run = load_tool_outputs(events_dir)
        for v in views.values():
            if v.run_id in outputs_by_run:
                attach_outputs(v, outputs_by_run[v.run_id])

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
        f"- tool output events: **{'available' if events_dir is not None else 'not provided (grounding checks degrade to needs_review)'}**",
        "- captured traffic is never golden; a graded comparison needs an explicit match to a human-reviewed golden case.",
    ]
    if res.provisional:
        out.append(
            "- **PROVISIONAL**: graded against golden references that are still `pending_human_review`. Correctness below is not an approved result."
        )

    # Grader v2 evaluation
    graded_turns: list[tuple[int, TurnView, GoldenCase, Turn, TurnOutcome, str]] = []
    gated_outcomes: dict[str, Counter[Outcome]] = {
        "execution": Counter(),
        "tool": Counter(),
        "answer": Counter(),
    }
    diag_outcomes: dict[str, Counter[Outcome]] = {
        "execution": Counter(),
        "tool": Counter(),
        "answer": Counter(),
    }
    fault_turns = 0
    basis_counts: dict[str, dict[str, Counter[Outcome]]] = {}

    for i, v in enumerate(ordered):
        target = link.get(v.run_id)
        if target is None:
            legacy_state = "candidate only (no confirmed match)"
        else:
            case = by_id[target[0]]
            turn = case.turns[target[1]]
            if target in res.matched:
                g = grade_turn(target[0], target[1], turn, v)
                checks = ", ".join(
                    f"{k}={'pass' if ok else 'FAIL'}" for k, ok in g.checks.items()
                )
                legacy_state = f"{'provisional ' if res.provisional else ''}{'PASS' if g.passed else 'FAIL'}: {checks}"
                v2 = grade_turn_v2(
                    target[0],
                    target[1],
                    turn,
                    v,
                    tags=case.tags,
                    tool_output_available=(events_dir is not None),
                )
                graded_turns.append((i, v, case, turn, v2, legacy_state))
                is_diag = "diagnostic" in case.tags
                is_fault = FAULT_INJECTION_TAG in case.tags
                if is_fault:
                    fault_turns += 1
                elif is_diag:
                    diag_outcomes["execution"][v2.execution] += 1
                    diag_outcomes["tool"][v2.tool] += 1
                    diag_outcomes["answer"][v2.answer] += 1
                else:
                    gated_outcomes["execution"][v2.execution] += 1
                    gated_outcomes["tool"][v2.tool] += 1
                    gated_outcomes["answer"][v2.answer] += 1
                    b = _basis_tag(case.tags)
                    b_counters = basis_counts.setdefault(
                        b, {"tool": Counter(), "answer": Counter()}
                    )
                    b_counters["tool"][v2.tool] += 1
                    b_counters["answer"][v2.answer] += 1
            else:
                legacy_state = (
                    "candidate only (golden case pending_human_review; not graded)"
                )

    if graded_turns:
        out += [
            "",
            "## Grader Outcomes (v2)",
            "",
            "### Three Outcome Rates (Gated)",
            "",
            "| Dimension | Correct | Incorrect | Needs Review | Not Applicable | Total Gated |",
            "|---|---|---|---|---|---|",
        ]
        for dim in ("execution", "tool", "answer"):
            c = gated_outcomes[dim]
            tot = sum(c.values())
            out.append(
                f"| {dim.capitalize()} | {c['correct']} | {c['incorrect']} | {c['needs_review']} | {c['not_applicable']} | {tot} |"
            )

        if any(diag_outcomes["execution"].values()):
            out += [
                "",
                "### Diagnostic Cases (Reported Separately)",
                "",
                "| Dimension | Correct | Incorrect | Needs Review | Not Applicable | Total Diagnostic |",
                "|---|---|---|---|---|---|",
            ]
            for dim in ("execution", "tool", "answer"):
                c = diag_outcomes[dim]
                tot = sum(c.values())
                out.append(
                    f"| {dim.capitalize()} | {c['correct']} | {c['incorrect']} | {c['needs_review']} | {c['not_applicable']} | {tot} |"
                )

        if fault_turns > 0:
            out += [
                "",
                f"### Fault-Injection Cases: {fault_turns} turn(s) (not live-executable, marked not_applicable)",
            ]

        if basis_counts:
            out += [
                "",
                "### Tool Basis Breakdown (Gated)",
                "",
                "| Basis | Turns | Tool Correct | Tool Incorrect | Tool Needs Review | Answer Correct | Answer Incorrect | Answer Needs Review |",
                "|---|---|---|---|---|---|---|---|",
            ]
            for b, b_c in sorted(basis_counts.items()):
                n_turns = sum(b_c["tool"].values())
                tc, ti, tn = (
                    b_c["tool"]["correct"],
                    b_c["tool"]["incorrect"],
                    b_c["tool"]["needs_review"],
                )
                ac, ai, an = (
                    b_c["answer"]["correct"],
                    b_c["answer"]["incorrect"],
                    b_c["answer"]["needs_review"],
                )
                out.append(
                    f"| {b} | {n_turns} | {tc} | {ti} | {tn} | {ac} | {ai} | {an} |"
                )

    out += [
        "",
        "## Turns",
        "",
        "| # | question | linked golden turn | execution | tool | answer | legacy grading | tools (actual) | tools expected | reasons |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    notes: list[str] = []
    graded_by_idx = {g[0]: g for g in graded_turns}
    for i, v in enumerate(ordered):
        target = link.get(v.run_id)
        if target is None:
            exec_res, tool_res, ans_res, reasons, legacy_state = (
                "-",
                "-",
                "-",
                "-",
                "candidate only (no confirmed match)",
            )
            golden, expected = "-", "-"
        else:
            case = by_id[target[0]]
            turn = case.turns[target[1]]
            golden = f"`{target[0]}#{target[1]}`"
            expected = ", ".join(e.name for e in turn.expected_tools) or "none"
            if i in graded_by_idx:
                _, _, _, _, v2, legacy_state = graded_by_idx[i]
                exec_res, tool_res, ans_res = v2.execution, v2.tool, v2.answer
                reasons = "; ".join(v2.reasons) or "none"
            else:
                exec_res, tool_res, ans_res, reasons = "-", "-", "-", "not graded"
                legacy_state = (
                    "candidate only (golden case pending_human_review; not graded)"
                )
        out.append(
            f"| {i} | {v.question or '(content not captured)'} | {golden} | {exec_res} | {tool_res} | {ans_res} | {legacy_state} | {_tools(v)} | {expected} | {reasons} |"
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
        *_prompt_lines(calls_all),
        "Judge metrics: not run on captured traffic (no recorded judge verdicts exist for it).",
        "",
    ]
    return "\n".join(out)


def _prompt_lines(calls: list) -> list[str]:
    """Prompt revisions per role, from per-call capture fields (nothing when absent)."""
    seen: dict[str, set[str]] = {}
    for c in calls:
        if c.prompt_template_id:
            rev = f"{c.prompt_template_id}@{c.prompt_template_version or '?'}"
            seen.setdefault(c.role or "?", set()).add(rev)
    if not seen:
        return []
    return [
        "Prompt revisions by role: "
        + "; ".join(
            f"{role}: {', '.join(sorted(revs))}" for role, revs in sorted(seen.items())
        )
        + "."
    ]
