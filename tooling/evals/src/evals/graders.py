"""Deterministic graders. No model is involved; ground truth comes from reviewed reference SQL."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from evals.schema import ExpectedTool, Fact, Turn
from evals.traces import ToolOutput, ToolUse, TurnView

_NUMBER = re.compile(
    r"(?<![\w.])-?\$?\d[\d,]*(?:\.\d+)?%?(?:\s*(?:million|thousand|k|m)\b)?",
    re.IGNORECASE,
)
_YEAR = re.compile(r"^(19|20)\d{2}$")


def extract_numbers(text: str) -> list[float]:
    out: list[float] = []
    for m in _NUMBER.finditer(text):
        raw = (
            m.group(0)
            .lower()
            .replace("$", "")
            .replace(",", "")
            .replace("%", "")
            .strip()
        )
        scale = 1.0
        for word, mult in (("million", 1e6), ("thousand", 1e3), ("m", 1e6), ("k", 1e3)):
            if raw.endswith(word):
                raw, scale = raw[: -len(word)].strip(), mult
                break
        try:
            out.append(float(raw) * scale)
        except ValueError:
            continue
    return out


def number_matches(expected: float, text: str, rel_tol: float) -> bool:
    candidates = extract_numbers(text)
    # A rate may be reported as 23.8% when the reference is 0.2376.
    targets = [expected, expected * 100] if abs(expected) < 1 else [expected]
    for target in targets:
        for got in candidates:
            if abs(got - target) <= max(
                abs(target) * rel_tol, 0.005 if rel_tol else 0.0
            ):
                return True
    return False


def grade_fact(fact: Fact, answer: str) -> bool:
    if fact.value is None:
        raise ValueError(
            f"fact {fact.label} has no reference value; run refresh-reference"
        )
    if fact.kind == "text":
        return str(fact.value).lower() in answer.lower()
    return number_matches(float(fact.value), answer, fact.rel_tol)


def fabricated_numbers(answer: str) -> list[float]:
    """Large or fractional numbers in an answer that must not contain data values."""
    bad = []
    for n in extract_numbers(answer):
        if n == int(n) and _YEAR.match(str(int(n))):
            continue
        if abs(n) >= 1000 or n != int(n):
            bad.append(n)
    return bad


def args_match(expected: ExpectedTool, actual: dict[str, object]) -> bool:
    exact = expected.match == "exact"

    def eq(a: object, b: object) -> bool:
        if isinstance(a, list) and isinstance(b, list):
            return sorted(map(str, a)) == sorted(map(str, b))
        if isinstance(a, dict) and isinstance(b, dict):
            if exact and a.keys() != b.keys():
                return False
            return all(k in b and eq(v, b[k]) for k, v in a.items())
        return a == b

    return eq(expected.arguments, actual)


def tool_score(expected: list[ExpectedTool], actual: list[ToolUse]) -> float:
    if not expected:
        return 1.0 if not actual else 0.0
    remaining = list(actual)
    hits = 0
    for exp in expected:
        for i, use in enumerate(remaining):
            if use.name == exp.name and args_match(exp, use.arguments):
                hits += 1
                del remaining[i]
                break
    return hits / len(expected)


@dataclass
class TurnGrade:
    case_id: str
    turn_index: int
    checks: dict[str, bool] = field(default_factory=dict)
    tool_score: float = 0.0
    missing: bool = False

    @property
    def passed(self) -> bool:
        return not self.missing and all(self.checks.values())


def grade_turn(
    case_id: str, index: int, turn: Turn, view: TurnView | None
) -> TurnGrade:
    grade = TurnGrade(case_id, index)
    if view is None:
        grade.missing = True
        return grade
    answer = view.answer or ""
    grade.tool_score = tool_score(turn.expected_tools, view.tools)
    grade.checks["tool_arguments"] = grade.tool_score == 1.0
    grade.checks["step_budget"] = view.steps <= turn.max_steps
    if turn.behavior == "answer":
        grade.checks["numeric_facts"] = bool(answer) and all(
            grade_fact(f, answer) for f in turn.reference.facts
        )
        grade.checks["evidence_present"] = any(t.ok and t.query_id for t in view.tools)
    else:
        low = answer.lower()
        grade.checks["states_limitation"] = any(
            m.lower() in low for m in turn.must_mention_any
        )
        grade.checks["no_fabricated_numbers"] = not fabricated_numbers(answer)
    return grade


# ---------------------------------------------------------------------------------------
# grade_turn_v2: three independent outcomes (execution, tool, answer). Heuristic only: no
# model is involved, and any check that needs tool output degrades to needs_review when the
# output is unavailable. needs_review is never a pass.
# ---------------------------------------------------------------------------------------

Outcome = Literal["correct", "incorrect", "needs_review", "not_applicable"]

FAULT_INJECTION_TAG = "fault-injection"


@dataclass
class TurnOutcome:
    case_id: str
    turn_index: int
    execution: Outcome = "correct"
    tool: Outcome = "correct"
    answer: Outcome = "correct"
    reasons: list[str] = field(default_factory=list)


_ENTITY_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "the",
        "of",
        "in",
        "on",
        "by",
        "per",
        "at",
        "to",
        "for",
        "trips",
        "trip",
        "count",
        "counts",
        "avg",
        "average",
        "mean",
        "median",
        "fare",
        "fares",
        "tip",
        "tips",
        "minutes",
        "minute",
        "duration",
        "distance",
        "rate",
        "share",
        "top",
        "peak",
        "busiest",
        "hour",
        "hours",
        "zone",
        "zones",
        "row",
        "range",
        "distinct",
        "example",
        "total",
        "amount",
        "volume",
        "first",
        "last",
        "week",
        "weekday",
        "weekend",
        "bucket",
        "borough",
        "boroughs",
        "longest",
        "shortest",
        "highest",
        "lowest",
        "most",
        "least",
        "second",
        "third",
    ]
)
_SUPER_MAX = r"longest|highest|most|largest|biggest|greatest|busiest|maximum"
_SUPER_MIN = r"shortest|lowest|least|smallest|fewest|minimum|cheapest"
_SUPER = re.compile(rf"\b({_SUPER_MAX}|{_SUPER_MIN})\b", re.IGNORECASE)
_CLAUSE_SPLIT = re.compile(
    r"[;\n]|\.\s|,\s|\s(?:while|whereas|but)\s|\sand\s(?=the\s)", re.IGNORECASE
)
_RANKING_Q = re.compile(
    r"\b(top|most|least|highest|lowest|busiest|longest|shortest|best|worst|largest|"
    r"smallest|peak|biggest|fewest)\b",
    re.IGNORECASE,
)
_TRUNC_ACK = re.compile(
    r"truncat|\blimit(?:ed)?\b|\bpartial|cut off|\bfirst \d+|\btop\b|"
    r"only (?:the )?(?:first|top)|more rows",
    re.IGNORECASE,
)
_WORD = re.compile(r"[a-z]+")


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def _numeric_cells(output: ToolOutput) -> list[float]:
    """Every number a tool output can legitimately ground, including raw metadata."""
    found: list[float] = []

    def walk(value: object) -> None:
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, (int, float)):
            found.append(float(value))
        elif isinstance(value, str):
            found.extend(extract_numbers(value))
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)

    walk(output.rows)
    walk(output.raw)
    if output.row_count is not None:
        found.append(float(output.row_count))
    return found


def _close(n: float, p: float) -> bool:
    tol = 0.01 if n >= 1e5 and n % 1e4 == 0 else 0.005
    return abs(n - p) <= max(abs(p) * tol, 0.0051)


def ungrounded_numbers(
    answer: str, pool: list[float], question: str = ""
) -> list[float]:
    """Answer numbers (years and small integers ignored) absent from the tool output."""
    allowed = pool + extract_numbers(question)
    bad = []
    for n in fabricated_numbers(answer):
        if not any(
            _close(n, p) or (abs(p) < 1 and _close(n, p * 100)) for p in allowed
        ):
            bad.append(n)
    return bad


def _segments(answer: str) -> list[str]:
    """Clauses plus indented bullet groups: the scope in which a label and value co-occur."""
    segs = [c for c in _CLAUSE_SPLIT.split(answer) if c and c.strip()]
    lines = answer.splitlines()

    def indent(line: str) -> int:
        return len(line) - len(line.lstrip())

    i = 0
    while i < len(lines):
        j = i + 1
        while j < len(lines) and (
            not lines[j].strip() or indent(lines[j]) > indent(lines[i])
        ):
            j += 1
        if j - i > 1 and lines[i].strip():
            segs.append("\n".join(lines[i:j]))
        i += 1
    return segs


def _entity_tokens(fact: Fact) -> set[str]:
    return {
        t
        for t in re.split(r"[^a-z0-9]+", fact.label.lower())
        if len(t) > 1 and not t.isdigit() and t not in _ENTITY_STOP
    }


def check_entity_value_association(
    facts: list[Fact], answer: str
) -> tuple[list[str], list[str]]:
    """(incorrect reasons, needs_review reasons): a labelled number must sit in the same
    clause or bullet group as its label, else the value is attached to the wrong entity."""
    bad: list[str] = []
    unsure: list[str] = []
    segs = _segments(answer)
    for fact in facts:
        if fact.kind != "number" or fact.value is None:
            continue
        tokens = _entity_tokens(fact)
        value = float(fact.value)
        if value == int(value) and abs(value) < 100:
            continue  # small integers match unrelated text too easily
        if not tokens or not number_matches(value, answer, fact.rel_tol):
            continue
        with_entity = [s for s in segs if tokens & _words(s)]
        if not with_entity:
            continue  # label never named: nothing to misattribute
        if any(number_matches(float(fact.value), s, fact.rel_tol) for s in with_entity):
            continue
        name = " ".join(sorted(tokens))
        if any(extract_numbers(s) for s in with_entity):
            bad.append(
                f"association: {name} is paired with other numbers, not {fact.value}"
            )
        else:
            unsure.append(f"association: {name} not near its value {fact.value}")
    return bad, unsure


def _numeric_columns(output: ToolOutput) -> dict[int, list[float | None]]:
    cols: dict[int, list[float | None]] = {}
    width = max((len(r) for r in output.rows), default=0)
    for c in range(1, width):
        vals = [r[c] if c < len(r) else None for r in output.rows]
        if all(
            v is None or (isinstance(v, (int, float)) and not isinstance(v, bool))
            for v in vals
        ) and any(v is not None for v in vals):
            cols[c] = [None if v is None else float(v) for v in vals]
    return cols


def check_comparative_claims(answer: str, output: ToolOutput) -> list[str]:
    """Superlative claims ("X has the longest average trip distance") checked against rows."""
    if len(output.rows) < 2 or not output.columns:
        return []
    labels = {
        str(r[0]): i for i, r in enumerate(output.rows) if r and isinstance(r[0], str)
    }
    if len(labels) < 2:
        return []
    ordered = sorted(labels, key=len, reverse=True)
    numeric = _numeric_columns(output)
    names = {
        c: _words(str(output.columns[c]).replace("_", " "))
        for c in numeric
        if c < len(output.columns)
    }
    generic = {"average", "avg", "amount", "total"}
    out: list[str] = []
    current: str | None = None
    had_super = False
    for clause in _CLAUSE_SPLIT.split(answer):
        if not clause or not clause.strip():
            continue
        low = clause.lower()
        found = next(
            (
                lab
                for lab in ordered
                if re.search(rf"\b{re.escape(lab.lower())}\b", low)
            ),
            None,
        )
        sup = _SUPER.search(clause)
        if found:
            current = found
        elif not (had_super and sup):
            current = None  # a label carries only into an immediately following claim
        had_super = bool(sup)
        if not sup or current is None:
            continue
        words = _words(clause)
        if "trips" in words or "volume" in words:
            words |= {"trip", "count"}
        best = [
            (len(toks - generic), c)
            for c, toks in names.items()
            if (toks - generic) and (toks - generic) <= words
        ]
        if not best:
            continue
        best.sort(reverse=True)
        if len(best) > 1 and best[0][0] == best[1][0]:
            continue  # ambiguous metric: not derivable
        col = best[0][1]
        values = numeric[col]
        mine = values[labels[current]]
        known = [v for v in values if v is not None]
        if mine is None:
            continue
        is_max = re.match(_SUPER_MAX, sup.group(1), re.IGNORECASE) is not None
        target = max(known) if is_max else min(known)
        if mine != target:
            winner = next(lab for lab, i in labels.items() if values[i] == target)
            out.append(
                f"comparative: {current} called {sup.group(1).lower()} on "
                f"{output.columns[col]} but rows show {winner} ({target})"
            )
    return out


def _truncated_incomplete(use: ToolUse) -> bool:
    out = use.output
    if out is None:
        return False
    limit = use.arguments.get("limit")
    return bool(out.truncated) or (
        isinstance(limit, int) and out.row_count is not None and out.row_count == limit
    )


def _grade_tools(
    turn: Turn, view: TurnView, facts_verified: bool, reasons: list[str]
) -> Outcome:
    actual = view.tools
    if not turn.expected_tools:
        if actual:
            reasons.append(
                "tool: unneeded call(s) " + ", ".join(sorted({t.name for t in actual}))
            )
            return "incorrect"
        return "correct"
    remaining = list(actual)
    unmet: list[ExpectedTool] = []
    for exp in turn.expected_tools:
        hit = None
        for i, use in enumerate(remaining):
            if use.name == exp.name and args_match(exp, use.arguments):
                hit = i
                break
        if hit is None:
            unmet.append(exp)
            continue
        use = remaining.pop(hit)
        for key in exp.required_args:
            if use.arguments.get(key) is None:
                reasons.append(f"tool: required arg {key} missing on {use.name}")
                return "incorrect"
        for key in exp.forbidden_args:
            if use.arguments.get(key) is not None:
                reasons.append(
                    f"tool: {key}={use.arguments[key]!r} set on {use.name} but not asked for"
                )
                return "incorrect"
    if not unmet:
        return "correct"
    names = {t.name for t in actual}
    if all(e.name not in names for e in unmet) and facts_verified:
        reasons.append(
            "tool: equivalence: "
            + ", ".join(sorted(names))
            + " instead of "
            + ", ".join(e.name for e in unmet)
            + "; reference value verified in the other tool's output"
        )
        return "needs_review"
    reasons.append(
        "tool: expected " + ", ".join(e.name for e in unmet) + " not matched"
    )
    return "incorrect"


def _worst(levels: list[Outcome]) -> Outcome:
    if "incorrect" in levels:
        return "incorrect"
    if "needs_review" in levels:
        return "needs_review"
    return "correct"


def grade_turn_v2(
    case_id: str,
    index: int,
    turn: Turn,
    view: TurnView | None,
    *,
    tags: tuple[str, ...] | list[str] = (),
    tool_output_available: bool,
) -> TurnOutcome:
    res = TurnOutcome(case_id, index)
    if FAULT_INJECTION_TAG in tags:
        res.execution = res.tool = res.answer = "not_applicable"
        res.reasons.append("fault-injection case: not live-executable")
        return res
    if view is None:
        res.execution = "incorrect"
        res.tool = res.answer = "not_applicable"
        res.reasons.append("execution: no captured run for this turn")
        return res
    answer = view.answer or ""
    if view.errored:
        res.execution = "incorrect"
        res.reasons.append("execution: model or tool error recorded")
    if view.steps > turn.max_steps:
        res.execution = "incorrect"
        res.reasons.append(f"execution: {view.steps} steps > budget {turn.max_steps}")

    ok_tools = [t for t in view.tools if t.ok]
    outputs_known = bool(ok_tools) and all(t.output is not None for t in ok_tools)
    available = tool_output_available and outputs_known
    outputs = [t.output for t in ok_tools if t.output is not None] if available else []
    pool = [n for o in outputs for n in _numeric_cells(o)]

    facts = [f for f in turn.reference.facts if f.value is not None]
    facts_found = bool(answer) and all(grade_fact(f, answer) for f in facts)
    facts_verified = (
        bool(facts)
        and facts_found
        and available
        and all(
            any(_close(float(f.value), p) for p in pool)
            for f in facts
            if f.kind == "number"
        )
    )
    res.tool = _grade_tools(turn, view, facts_verified, res.reasons)
    if (
        res.tool == "incorrect"
        and turn.expected_tools
        and facts
        and facts_found
        and not available
        and all(e.name not in {t.name for t in view.tools} for e in turn.expected_tools)
    ):
        res.tool = "needs_review"
        res.reasons.append("tool: equivalence unverifiable: tool output unavailable")

    if turn.behavior == "answer" and res.tool == "correct" and available:
        for use in ok_tools:
            if _truncated_incomplete(use) and not _RANKING_Q.search(
                view.question or turn.question
            ):
                res.tool = "incorrect"
                res.reasons.append(
                    f"tool: {use.name} result truncated (limit {use.arguments.get('limit')}) "
                    "for a question that is not a top-N ranking"
                )
    levels: list[Outcome] = []
    if turn.behavior == "answer" or facts:
        if not facts_found:
            levels.append("incorrect")
            res.reasons.append("answer: reference fact(s) not found in answer")
        if turn.behavior == "answer" and not ok_tools:
            levels.append("incorrect")
            res.reasons.append("answer: no successful tool call as evidence")
    if turn.behavior != "answer":
        low = answer.lower()
        if any(m.lower() in low for m in turn.must_mention_any):
            levels.append("correct")
        else:
            levels.append("needs_review")
            res.reasons.append("answer: no limitation phrase; needs a human read")

    big_numbers = fabricated_numbers(answer)
    needs_output = turn.behavior == "answer" or bool(big_numbers)
    if view.tools and needs_output and not available:
        levels.append("needs_review")
        res.reasons.append("answer: tool output unavailable; grounding checks skipped")
    elif available:
        bad, unsure = check_entity_value_association(facts, answer)
        for r in bad:
            levels.append("incorrect")
            res.reasons.append("answer: " + r)
        for r in unsure:
            levels.append("needs_review")
            res.reasons.append("answer: " + r)
        for o in outputs:
            for r in check_comparative_claims(answer, o):
                levels.append("incorrect")
                res.reasons.append("answer: " + r)
        if turn.behavior == "answer":
            for use in ok_tools:
                if (
                    _truncated_incomplete(use)
                    and not _RANKING_Q.search(view.question or turn.question)
                    and not _TRUNC_ACK.search(answer)
                ):
                    levels.append("incorrect")
                    res.reasons.append(
                        f"answer: truncated result from {use.name} presented as complete"
                    )
        bad_nums = ungrounded_numbers(answer, pool, view.question or turn.question)
        if turn.behavior != "answer" and ok_tools and big_numbers and not bad_nums:
            levels.append("needs_review")
            res.reasons.append(
                "answer: presents tool data for an unsupported request; needs a human read"
            )
        if bad_nums:
            level: Outcome = (
                "incorrect" if turn.behavior != "answer" else "needs_review"
            )
            levels.append(level)
            res.reasons.append(
                f"answer: numbers not in tool output (fabricated?): {bad_nums[:5]}"
            )
    elif big_numbers and not view.tools:
        levels.append("incorrect")
        res.reasons.append(f"answer: numbers {big_numbers[:5]} with no tool evidence")

    res.answer = _worst(levels) if levels else "correct"
    return res
