"""Ingest capture.v1 JSONL into per-turn views that graders and reports consume."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from evals.schema import CAPTURE_SCHEMA_VERSION, TraceRecord


class TraceError(ValueError):
    pass


@dataclass
class ToolUse:
    name: str
    arguments: dict[str, object]
    status: str | None
    query_id: str | None
    row_count: int | None


@dataclass
class TurnView:
    run_id: str
    case_id: str
    turn_index: int
    question: str | None = None
    answer: str | None = None
    tools: list[ToolUse] = field(default_factory=list)
    model_calls: list[TraceRecord] = field(default_factory=list)

    @property
    def steps(self) -> int:
        return len(self.tools)

    @property
    def errored(self) -> bool:
        return any(m.error for m in self.model_calls) or any(
            t.status not in (None, "ok") for t in self.tools
        )

    def _sum(self, attr: str) -> int | None:
        values = [getattr(m, attr) for m in self.model_calls]
        known = [v for v in values if v is not None]
        return sum(known) if known else None  # never report 0 for "unknown"

    @property
    def input_tokens(self) -> int | None:
        return self._sum("input_tokens")

    @property
    def output_tokens(self) -> int | None:
        return self._sum("output_tokens")

    @property
    def cost_usd(self) -> float | None:
        known = [m.cost_usd for m in self.model_calls if m.cost_usd is not None]
        return round(sum(known), 6) if known else None

    @property
    def latency_ms(self) -> float | None:
        known = [m.latency_ms for m in self.model_calls if m.latency_ms is not None]
        return sum(known) if known else None


def load_records(path: Path) -> list[TraceRecord]:
    records: list[TraceRecord] = []
    seen: set[str] = set()
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        rec = TraceRecord.model_validate_json(line)
        if rec.schema_version != CAPTURE_SCHEMA_VERSION:
            raise TraceError(
                f"{path}:{lineno}: unsupported schema {rec.schema_version}"
            )
        if rec.call_id in seen:  # call_id is the idempotency key
            continue
        seen.add(rec.call_id)
        records.append(rec)
    return sorted(records, key=lambda r: (r.run_id, r.step))


def build_turns(records: list[TraceRecord]) -> dict[tuple[str, int], TurnView]:
    """Group records into turns keyed by (case_id, turn_index)."""
    turns: dict[tuple[str, int], TurnView] = {}
    for rec in records:
        if rec.case_id is None:
            raise TraceError(
                f"{rec.call_id}: record has no case_id (not linked to a golden case)"
            )
        key = (rec.case_id, rec.turn_index)
        view = turns.setdefault(key, TurnView(rec.run_id, rec.case_id, rec.turn_index))
        if rec.record_type == "tool_call":
            view.tools.append(
                ToolUse(
                    rec.tool_name or "",
                    rec.arguments or {},
                    rec.status,
                    rec.query_id,
                    rec.row_count,
                )
            )
        else:
            view.model_calls.append(rec)
            if rec.question:
                view.question = rec.question
            if rec.answer:
                view.answer = rec.answer
    return turns


def load_turns(path: Path) -> dict[tuple[str, int], TurnView]:
    return build_turns(load_records(path))
