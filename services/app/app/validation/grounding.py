"""Deterministic answer-grounding checks.

Facts a reader could mistake for dataset results (numbers, percentages,
money, query ids) must trace back to the governed tool observation for the
turn. Causal language the data cannot support is flagged. Subjective quality
(clarity, helpfulness) is NOT judged here; that stays with the calibrated
LLM judge.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

_NUMBER = re.compile(
    r"(?<![\w.])(?P<money>\$)?(?P<num>\d[\d,]*(?:\.\d+)?)"
    r"(?P<suffix>\s*%|\s*(?:million|thousand|billion|k|m|b)\b)?",
    re.IGNORECASE,
)
_SCALES = {
    "k": 1e3,
    "thousand": 1e3,
    "m": 1e6,
    "million": 1e6,
    "b": 1e9,
    "billion": 1e9,
}
_QUERY_ID = re.compile(
    r"query[\s_-]?id\W{0,3}([A-Za-z0-9][A-Za-z0-9_.:-]{2,})", re.IGNORECASE
)
_CAUSAL = re.compile(
    r"\b(because|due to|caused by|causes?|as a result of|results? from|leads? to|"
    r"led to|driven by|thanks to|owing to|attributable to|explained by)\b",
    re.IGNORECASE,
)
_HEDGE = re.compile(
    r"\b(may|might|could|possibly|perhaps|cannot|can't|can not|unclear|unknown|"
    r"does not (?:show|say|establish)|not (?:clear|known)|no evidence|suggests?)\b",
    re.IGNORECASE,
)
_IGNORED_TOP_LEVEL = {"execution_duration_ms", "query_id", "truncated"}
MAX_DERIVED_ROWS = 20


@dataclass(frozen=True)
class _Token:
    text: str
    value: float
    tolerance: float
    percent: bool
    must_check: bool


@dataclass
class GroundingReport:
    ungrounded_numbers: list[float] = field(default_factory=list)
    ungrounded_tokens: list[str] = field(default_factory=list)
    unknown_query_ids: list[str] = field(default_factory=list)
    causal_claims: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Numeric and evidence-id checks. Causal flags are advisory, not blocking."""
        return not self.ungrounded_numbers and not self.unknown_query_ids

    @property
    def clean(self) -> bool:
        return self.ok and not self.causal_claims

    def to_metadata(self) -> dict[str, object]:
        return {
            "ungrounded_numbers": self.ungrounded_numbers,
            "ungrounded_tokens": self.ungrounded_tokens,
            "unknown_query_ids": self.unknown_query_ids,
            "causal_claims": self.causal_claims,
        }

    def caveat(self) -> str | None:
        parts: list[str] = []
        if self.ungrounded_tokens:
            parts.append(
                "these figures could not be verified against the governed query "
                f"result and should not be relied on: {', '.join(self.ungrounded_tokens)}"
            )
        if self.unknown_query_ids:
            parts.append(
                "cited query id(s) do not match this run's evidence: "
                f"{', '.join(self.unknown_query_ids)}"
            )
        if self.causal_claims:
            parts.append(
                "causal claims are not established by this dataset and are unverified"
            )
        if not parts:
            return None
        return "Note: " + "; ".join(parts) + "."


def _tokens(text: str) -> list[_Token]:
    tokens: list[_Token] = []
    for match in _NUMBER.finditer(text):
        raw = match.group("num")
        try:
            base = float(raw.replace(",", ""))
        except ValueError:
            continue
        suffix = (match.group("suffix") or "").strip().lower()
        percent = suffix == "%"
        scale = _SCALES.get(suffix, 1.0)
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        value = base * scale
        is_year = 1900 <= base <= 2100 and "," not in raw and decimals == 0
        small_int = decimals == 0 and scale == 1.0 and 0 <= base <= 31
        must_check = bool(match.group("money")) or percent or not (is_year or small_int)
        tokens.append(
            _Token(
                text=match.group(0).strip(),
                value=value,
                tolerance=0.5 * 10**-decimals * scale,
                percent=percent,
                must_check=must_check,
            )
        )
    return tokens


def _numeric_leaves(node: object, key: str | None = None) -> list[float]:
    if key in _IGNORED_TOP_LEVEL:
        return []
    if isinstance(node, bool):
        return []
    if isinstance(node, (int, float)):
        return [float(node)]
    if isinstance(node, Mapping):
        return [v for k, item in node.items() for v in _numeric_leaves(item, str(k))]
    if isinstance(node, list):
        return [v for item in node for v in _numeric_leaves(item)]
    return []


def _derived(evidence: Mapping[str, object]) -> list[float]:
    rows = evidence.get("rows")
    if not isinstance(rows, list):
        return []
    rows = rows[:MAX_DERIVED_ROWS]
    width = max((len(r) for r in rows if isinstance(r, list)), default=0)
    out: list[float] = []
    for col in range(width):
        values = [
            float(r[col])
            for r in rows
            if isinstance(r, list)
            and col < len(r)
            and isinstance(r[col], (int, float))
            and not isinstance(r[col], bool)
        ]
        if len(values) < 2:
            continue
        total = sum(values)
        out += [total, total / len(values)]
        for a in values:
            if total:
                out.append(a / total * 100)
            for b in values:
                if a is b:
                    continue
                out += [a + b, abs(a - b)]
                if b:
                    out += [a / b, a / b * 100, (a - b) / b * 100]
    return out


def _supported(token: _Token, direct: list[float], derived: list[float]) -> bool:
    targets = [token.value] + ([token.value / 100] if token.percent else [])
    candidates = direct + derived
    return any(
        abs(target - candidate) <= token.tolerance + 1e-9
        for target in targets
        for candidate in candidates
    )


def check_answer_grounding(
    answer: str,
    evidence: Mapping[str, object] | None,
    *,
    question: str = "",
) -> GroundingReport:
    report = GroundingReport()
    direct = _numeric_leaves(evidence) if evidence else []
    derived = _derived(evidence) if evidence else []
    asked = [t.value for t in _tokens(question)]
    # Cited ids are checked separately; their digits are not data values.
    for token in _tokens(_QUERY_ID.sub(" ", answer)):
        if not token.must_check:
            continue
        if any(abs(token.value - a) <= token.tolerance + 1e-9 for a in asked):
            continue
        if not _supported(token, direct, derived):
            report.ungrounded_numbers.append(token.value)
            report.ungrounded_tokens.append(token.text)
    known_id = evidence.get("query_id") if evidence else None
    for match in _QUERY_ID.finditer(answer):
        cited = match.group(1).rstrip(".:,;-")
        if cited != known_id:
            report.unknown_query_ids.append(cited)
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", answer):
        if _CAUSAL.search(sentence) and not _HEDGE.search(sentence):
            report.causal_claims.append(sentence.strip()[:160])
    return report
