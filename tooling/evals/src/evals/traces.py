"""Ingest capture.v1 JSONL into per-turn views that graders and reports consume."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from evals.schema import CAPTURE_SCHEMA_VERSION, TraceRecord


class TraceError(ValueError):
    pass


# Synthetic fixtures use "ok"; the app's durable run steps use "completed".
TOOL_OK = frozenset({"ok", "completed"})


@dataclass
class ToolOutput:
    """Result of one tool call, read from run events (capture.v1 carries no rows)."""

    columns: list[object]
    rows: list[list[object]]
    truncated: bool | None
    row_count: int | None
    query_id: str | None
    raw: dict[str, object] = field(default_factory=dict)


@dataclass
class ToolUse:
    name: str
    arguments: dict[str, object]
    status: str | None
    query_id: str | None
    row_count: int | None
    latency_ms: float | None = None
    output: ToolOutput | None = None  # None means unknown, never guessed

    @property
    def ok(self) -> bool:
        return self.status in TOOL_OK


@dataclass
class TurnView:
    run_id: str
    case_id: str | None
    turn_index: int
    conversation_id: str | None = None
    started_at: str | None = None
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
            t.status is not None and not t.ok for t in self.tools
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


def _parse_output(content: str) -> ToolOutput | None:
    try:
        payload = json.loads(content)
    except ValueError:
        return None  # assistant/user text, not a tool result
    if not isinstance(payload, dict) or not payload.get("query_id"):
        return None
    rows = payload.get("rows")
    columns = payload.get("columns")
    return ToolOutput(
        columns=columns if isinstance(columns, list) else [],
        rows=rows if isinstance(rows, list) else [],
        truncated=(
            payload.get("truncated")
            if isinstance(payload.get("truncated"), bool)
            else None
        ),
        row_count=(
            payload.get("row_count")
            if isinstance(payload.get("row_count"), int)
            else None
        ),
        query_id=str(payload["query_id"]),
        raw=payload,
    )


def load_tool_outputs(events_dir: Path) -> dict[str, list[ToolOutput]]:
    """Parse ``run_<id>.txt`` SSE event files into tool outputs per run.

    Tool results appear in ``context.reduced`` events as JSON ``recent_messages``
    content with role="tool". Non-JSON messages are skipped; repeated results are
    de-duplicated by query_id. Conflicting duplicates mark the query ambiguous
    and are discarded to prevent corrupted evidence from certifying scores.
    """
    out: dict[str, list[ToolOutput]] = {}
    for path in sorted(Path(events_dir).glob("run_*.txt")):
        seen: dict[str, ToolOutput] = {}
        conflicting: set[str] = set()
        for line in path.read_text().splitlines():
            if not line.startswith("data: ") or "context.reduced" not in line:
                continue
            try:
                event = json.loads(line[6:])
            except ValueError:
                continue
            if event.get("event_type") != "context.reduced":
                continue
            ev_run_id = event.get("run_id")
            if ev_run_id and ev_run_id != path.stem:
                continue
            context = (event.get("payload") or {}).get("working_context") or {}
            for msg in context.get("recent_messages") or []:
                if msg.get("role") != "tool":
                    continue
                parsed = _parse_output(str(msg.get("content", "")))
                if not parsed or not parsed.query_id:
                    continue
                qid = parsed.query_id
                if qid in seen:
                    prev = seen[qid]
                    if prev.rows != parsed.rows or prev.columns != parsed.columns:
                        conflicting.add(qid)
                else:
                    seen[qid] = parsed
        out[path.stem] = [o for qid, o in seen.items() if qid not in conflicting]
    return out


def attach_outputs(view: TurnView, outputs: list[ToolOutput]) -> TurnView:
    """Set ``ToolUse.output`` by query_id. Tools without a matching output stay None."""
    by_id = {o.query_id: o for o in outputs if o.query_id}
    for tool in view.tools:
        if tool.query_id and tool.query_id in by_id:
            tool.output = by_id[tool.query_id]
    return view


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


def _add(view: TurnView, rec: TraceRecord) -> None:
    if rec.record_type == "tool_call":
        view.tools.append(
            ToolUse(
                rec.tool_name or "",
                rec.arguments or {},
                rec.status,
                rec.query_id,
                rec.row_count,
                rec.latency_ms,
            )
        )
    else:
        view.model_calls.append(rec)
        if rec.question:
            view.question = rec.question
        if rec.answer:
            view.answer = rec.answer


def build_run_views(records: list[TraceRecord]) -> dict[str, TurnView]:
    """One view per captured run, keyed by run_id. No golden-case linkage is implied."""
    views: dict[str, TurnView] = {}
    for rec in records:
        view = views.setdefault(
            rec.run_id,
            TurnView(
                rec.run_id,
                None,
                0,
                conversation_id=rec.conversation_id,
                started_at=rec.run_started_at,
            ),
        )
        _add(view, rec)
    return views


def build_turns(records: list[TraceRecord]) -> dict[tuple[str, int], TurnView]:
    """Group records that already carry a (case_id, turn_index) link (synthetic fixtures).

    Records without a link (real capture) are skipped, not guessed: use ``build_run_views``
    plus ``evals.matching.resolve`` to link captured runs to reviewed golden cases."""
    turns: dict[tuple[str, int], TurnView] = {}
    for rec in records:
        if rec.case_id is None:
            continue
        key = (rec.case_id, rec.turn_index)
        view = turns.setdefault(key, TurnView(rec.run_id, rec.case_id, rec.turn_index))
        _add(view, rec)
    return turns


def load_turns(path: Path) -> dict[tuple[str, int], TurnView]:
    return build_turns(load_records(path))
