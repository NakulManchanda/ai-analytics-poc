"""Check that a run's Phoenix trace is complete and continuous (#226 live trace parity).

For every run in the frozen capture, the Phoenix trace named by the capture ``trace_id``
must hold exactly one ``ai.run`` root carrying the strategy/status attributes, every other
span must descend from it, and the LiteLLM and MCP spans the capture implies must exist.
Missing data is a problem, never a pass: an unreachable Phoenix, an empty trace or a
capture without a ``trace_id`` all fail the check.

Span names are those Phoenix actually exports (the REST span payload carries no service
name): ``ai.run`` (app), ``litellm_proxy_request`` (LiteLLM), ``mcp.tool.execute`` (MCP).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from evals.schema import TraceRecord

ROOT_SPAN = "ai.run"
LLM_SPAN = "litellm_proxy_request"
TOOL_SPAN = "mcp.tool.execute"
ROOT_ATTRS = (
    "ai.run_id",
    "ai.status",
    "agent.strategy",
    "agent.framework",
    "agent.step_count",
)

Fetch = Callable[[str], list[dict[str, Any]]]


class TraceCheckError(RuntimeError):
    """Phoenix could not be queried; the check cannot pass or fail on evidence."""


@dataclass
class RunTraceResult:
    run_id: str
    trace_id: str | None
    model_calls: int
    tool_calls: int  # completed capture tool calls (failed ones may never execute)
    spans: int = 0
    llm_spans: int = 0
    tool_spans: int = 0
    strategy: str | None = None
    problems: list[str] = field(default_factory=list)


@dataclass
class TraceCheck:
    runs: list[RunTraceResult] = field(default_factory=list)

    @property
    def problems(self) -> list[str]:
        return [f"{r.run_id}: {p}" for r in self.runs for p in r.problems]

    @property
    def ok(self) -> bool:
        return bool(self.runs) and not self.problems

    def to_json(self) -> dict[str, Any]:
        problems = self.problems or ([] if self.runs else ["no runs in capture"])
        return {
            "ok": self.ok,
            "problems": problems,
            "runs": [asdict(r) for r in self.runs],
        }


def _top_ancestor(
    span: dict[str, Any], by_id: dict[str, dict[str, Any]]
) -> dict[str, Any] | None:
    """The parentless span at the top of ``span``'s chain, or None if the chain is
    broken: a parent that is not in the trace, or a cycle."""
    seen = {span["context"]["span_id"]}
    while span.get("parent_id"):
        parent = span["parent_id"]
        if parent not in by_id or parent in seen:
            return None
        seen.add(parent)
        span = by_id[parent]
    return span


def check_run(result: RunTraceResult, spans: list[dict[str, Any]]) -> None:
    result.spans = len(spans)
    if not spans:
        result.problems.append("no spans in Phoenix for this trace")
        return
    roots = [s for s in spans if s.get("name") == ROOT_SPAN]
    if len(roots) != 1:
        result.problems.append(
            f"expected exactly one {ROOT_SPAN} span, found {len(roots)}"
        )
    by_id = {s["context"]["span_id"]: s for s in spans}
    detached = [
        s["name"]
        for s in spans
        if (top := _top_ancestor(s, by_id)) is None or top.get("name") != ROOT_SPAN
    ]
    if detached:
        result.problems.append(
            f"{len(detached)} spans are not under {ROOT_SPAN} (missing parent, cycle "
            "or another root): " + ", ".join(sorted(set(detached)))
        )
    if len(roots) == 1 and roots[0].get("parent_id"):
        result.problems.append(
            f"{ROOT_SPAN} has parent_id {roots[0]['parent_id']!r}; it must be the trace root"
        )
    if roots:
        attrs = roots[0].get("attributes") or {}
        result.strategy = attrs.get("agent.strategy")
        missing = [a for a in ROOT_ATTRS if attrs.get(a) in (None, "")]
        if missing:
            result.problems.append(
                f"{ROOT_SPAN} lacks attributes: {', '.join(missing)}"
            )
        if attrs.get("ai.run_id") not in (None, "", result.run_id):
            result.problems.append(
                f"{ROOT_SPAN} ai.run_id {attrs.get('ai.run_id')!r} != capture run_id"
            )
    result.llm_spans = sum(s.get("name") == LLM_SPAN for s in spans)
    result.tool_spans = sum(s.get("name") == TOOL_SPAN for s in spans)
    if result.llm_spans < result.model_calls:
        result.problems.append(
            f"{result.llm_spans} {LLM_SPAN} spans for {result.model_calls} capture model calls"
        )
    if result.tool_spans < result.tool_calls:
        result.problems.append(
            f"{result.tool_spans} {TOOL_SPAN} spans for {result.tool_calls} completed capture tool calls"
        )


def expected_runs(records: list[TraceRecord]) -> list[RunTraceResult]:
    runs: dict[str, RunTraceResult] = {}
    trace_ids: dict[str, set[str]] = {}
    for rec in records:
        run = runs.setdefault(
            rec.run_id, RunTraceResult(rec.run_id, None, model_calls=0, tool_calls=0)
        )
        run.trace_id = run.trace_id or rec.trace_id
        if rec.trace_id:
            trace_ids.setdefault(rec.run_id, set()).add(rec.trace_id)
        if rec.record_type == "model_call":
            run.model_calls += 1
        else:
            # A failed call may be rejected before the MCP tool runs and emit no execute
            # span; a completed call always has one (verified on 43 real runs), so only
            # completed calls set the minimum.
            run.tool_calls += rec.status == "completed"
    for run_id, ids in trace_ids.items():
        if len(ids) > 1:
            runs[run_id].problems.append(
                "capture records carry conflicting trace_ids: " + ", ".join(sorted(ids))
            )
    return sorted(runs.values(), key=lambda r: r.run_id)


def check_traces(records: list[TraceRecord], fetch: Fetch) -> TraceCheck:
    check = TraceCheck(runs=expected_runs(records))
    for run in check.runs:
        if run.problems:  # conflicting capture trace_ids: nothing sound to query
            continue
        if not run.trace_id:
            run.problems.append("capture records carry no trace_id")
            continue
        check_run(run, fetch(run.trace_id))
    return check


def phoenix_fetch(
    base_url: str, project: str = "default", timeout: float = 15
) -> Fetch:
    """Spans of one trace from Phoenix's REST API (paginated, errors are not passes)."""

    def fetch(trace_id: str) -> list[dict[str, Any]]:
        spans: list[dict[str, Any]] = []
        cursor = None
        while True:
            query = {"trace_id": trace_id, "limit": 1000}
            if cursor:
                query["cursor"] = cursor
            url = (
                f"{base_url.rstrip('/')}/v1/projects/{urllib.parse.quote(project, safe='')}"
                f"/spans?{urllib.parse.urlencode(query)}"
            )
            try:
                with urllib.request.urlopen(url, timeout=timeout) as resp:
                    page = json.load(resp)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as err:
                raise TraceCheckError(f"Phoenix query failed ({url}): {err}") from err
            spans += page.get("data") or []
            cursor = page.get("next_cursor")
            if not cursor:
                return spans

    return fetch
