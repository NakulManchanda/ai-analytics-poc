"""Deterministic graders. No model is involved; ground truth comes from reviewed reference SQL."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from evals.schema import ExpectedTool, Fact, Turn
from evals.traces import ToolUse, TurnView

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
    def eq(a: object, b: object) -> bool:
        if isinstance(a, list) and isinstance(b, list):
            return sorted(map(str, a)) == sorted(map(str, b))
        if isinstance(a, dict) and isinstance(b, dict):
            return a.keys() == b.keys() and all(eq(a[k], b[k]) for k in a)
        return a == b

    if expected.match == "exact":
        return eq(expected.arguments, actual)
    return all(k in actual and eq(v, actual[k]) for k, v in expected.arguments.items())


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
