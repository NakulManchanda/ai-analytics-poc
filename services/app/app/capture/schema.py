"""Versioned capture schema (``capture.v1``).

One record per model call or tool call, joined by ``run_id``. Model evidence and tool
evidence are separate record types; neither carries raw tool result rows.
"""

from __future__ import annotations

import hashlib
from typing import Any

SCHEMA_VERSION = "capture.v1"
RECORD_MODEL_CALL = "model_call"
RECORD_TOOL_CALL = "tool_call"


def sha256_hex(text: str | None) -> str | None:
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sort_key(record: dict[str, Any]) -> tuple[str, int, str]:
    return (
        str(record.get("run_id", "")),
        int(record.get("step") or 0),
        str(record.get("call_id", "")),
    )


def dedupe_key(record: dict[str, Any]) -> tuple[str, str]:
    return (str(record.get("run_id", "")), str(record.get("call_id", "")))
