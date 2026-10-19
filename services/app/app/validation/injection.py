"""Heuristic scan of tool-returned data for embedded instructions.

Tool observations are untrusted data. The structural defence is that nothing
in the app parses tool calls out of observations or answers, and every tool
call must pass ``validate_proposal`` plus MCP authorization. This scan is
only an *audit signal*: it records suspicious cells in the run trace; it
never edits values (that would break grounding) and never gates execution.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "override_instructions",
        re.compile(
            r"\b(ignore|disregard|forget)\b.{0,30}\b(previous|prior|above|earlier|all)\b"
            r".{0,30}\b(instruction|rule|prompt|message)s?\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        "role_marker",
        re.compile(r"(^|\s)(system|assistant|developer)\s*:", re.IGNORECASE),
    ),
    ("role_reassignment", re.compile(r"\byou are now\b", re.IGNORECASE)),
    (
        "tool_invocation",
        re.compile(
            r"\b(call|invoke|execute|run)\b.{0,20}\b(tool|function|aggregate_taxi_data|"
            r"query_taxi_data|compare_taxi_segments)\b",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    ("prompt_exfiltration", re.compile(r"\breveal\b.{0,30}\bprompt\b", re.IGNORECASE)),
    (
        "markup_injection",
        re.compile(r"</?\s*(tool|system|instructions?)\b", re.IGNORECASE),
    ),
)


@dataclass(frozen=True)
class ObservationFinding:
    row: int
    column: str
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {"row": self.row, "column": self.column, "reason": self.reason}


def scan_observation(query_result: Mapping[str, object]) -> list[ObservationFinding]:
    columns = query_result.get("columns")
    rows = query_result.get("rows")
    if not isinstance(columns, list) or not isinstance(rows, list):
        return []
    findings: list[ObservationFinding] = []
    for row_index, row in enumerate(rows):
        if not isinstance(row, list):
            continue
        for column, value in zip(columns, row, strict=False):
            if not isinstance(value, str):
                continue
            for reason, pattern in _PATTERNS:
                if pattern.search(value):
                    findings.append(ObservationFinding(row_index, str(column), reason))
                    break
    return findings
