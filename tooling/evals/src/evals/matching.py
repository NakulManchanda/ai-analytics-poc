"""Explicit linkage: captured run -> candidate case -> reviewed golden case -> comparison.

Captured traffic is never auto-labeled golden. ``suggest_candidates`` only *proposes* a case
(exact normalized question match); a human-edited matches file confirms the link, and a link only
yields a graded comparison when the golden case itself is human-reviewed (``review.status``).
Everything else stays a candidate.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ValidationError

from evals.schema import GoldenCase
from evals.traces import TurnView

MATCH_SCHEMA = "evals.matches.v1"


class MatchError(ValueError):
    pass


class Match(BaseModel):
    run_id: str
    case_id: str
    turn_index: int = 0
    confirmed_by: str


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower()).rstrip("?.! ")


@dataclass
class Candidate:
    run_id: str
    conversation_id: str | None
    question: str | None
    suggested: list[tuple[str, int]]  # every golden turn with the same question
    status: str = "candidate"  # never "golden"


def suggest_candidates(
    views: list[TurnView], cases: list[GoldenCase]
) -> list[Candidate]:
    index: dict[str, list[tuple[str, int]]] = {}
    for case in cases:
        for i, turn in enumerate(case.turns):
            index.setdefault(_norm(turn.question), []).append((case.id, i))
    return [
        Candidate(
            v.run_id,
            v.conversation_id,
            v.question,
            index.get(_norm(v.question), []) if v.question else [],
        )
        for v in sorted(views, key=lambda v: v.started_at or "")
    ]


def load_matches(path: Path, cases: list[GoldenCase]) -> dict[str, Match]:
    raw = json.loads(path.read_text())
    if raw.get("schema") != MATCH_SCHEMA:
        raise MatchError(f"{path}: expected schema {MATCH_SCHEMA}")
    by_id = {c.id: c for c in cases}
    out: dict[str, Match] = {}
    targets: set[tuple[str, int]] = set()
    for entry in raw.get("matches", []):
        try:
            m = Match.model_validate(entry)
        except ValidationError as err:
            raise MatchError(f"{path}: {err}") from err
        if not m.confirmed_by.strip():
            raise MatchError(f"{m.run_id}: confirmed_by is required")
        case = by_id.get(m.case_id)
        if case is None:
            raise MatchError(f"{m.run_id}: unknown case {m.case_id}")
        if not 0 <= m.turn_index < len(case.turns):
            raise MatchError(f"{m.run_id}: turn_index {m.turn_index} out of range")
        if m.run_id in out:
            raise MatchError(f"{m.run_id}: matched twice")
        if (m.case_id, m.turn_index) in targets:
            raise MatchError(f"{m.case_id}#{m.turn_index} matched twice")
        targets.add((m.case_id, m.turn_index))
        out[m.run_id] = m
    return out


@dataclass
class Resolution:
    matched: dict[tuple[str, int], TurnView] = field(default_factory=dict)
    # linked, but the golden case is still pending_human_review: not graded
    unreviewed: list[tuple[TurnView, Match]] = field(default_factory=list)
    candidate_only: list[TurnView] = field(default_factory=list)
    provisional: bool = False


def resolve(
    views: dict[str, TurnView],
    matches: dict[str, Match],
    cases: list[GoldenCase],
    provisional: bool = False,
) -> Resolution:
    """``provisional=True`` grades against pending references; the report must say so."""
    by_id = {c.id: c for c in cases}
    res = Resolution(provisional=provisional)
    for run_id, view in sorted(views.items(), key=lambda kv: kv[1].started_at or ""):
        m = matches.get(run_id)
        if m is None:
            res.candidate_only.append(view)
        elif by_id[m.case_id].review.status != "reviewed" and not provisional:
            res.unreviewed.append((view, m))
        else:
            view.case_id, view.turn_index = m.case_id, m.turn_index
            res.matched[(m.case_id, m.turn_index)] = view
    return res
