"""Append-only JSONL sink, one file per tenant. Never raises; failures are counted."""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Protocol

from app.capture.schema import dedupe_key

logger = logging.getLogger(__name__)
FILE_NAME = "captures.jsonl"
_TENANT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def validate_tenant(tenant_id: str) -> str:
    if not _TENANT_RE.match(tenant_id) or ".." in tenant_id:
        raise ValueError("invalid tenant_id")
    return tenant_id


def tenant_file(root: Path, tenant_id: str) -> Path:
    return Path(root) / validate_tenant(tenant_id) / FILE_NAME


class CaptureSink(Protocol):
    dropped: int

    def write(self, record: dict[str, Any]) -> bool: ...


class NullCaptureSink:
    dropped = 0

    def write(self, record: dict[str, Any]) -> bool:
        return False


class JsonlCaptureSink:
    def __init__(self, root: Path, tenant_id: str) -> None:
        self._path = tenant_file(root, tenant_id)
        self._lock = threading.Lock()
        self._seen: set[tuple[str, str]] | None = None
        self.dropped = 0

    def _load_seen(self) -> set[tuple[str, str]]:
        seen: set[tuple[str, str]] = set()
        if self._path.exists():
            for line in self._path.read_text(encoding="utf-8").splitlines():
                try:
                    seen.add(dedupe_key(json.loads(line)))
                except ValueError:
                    continue
        return seen

    def write(self, record: dict[str, Any]) -> bool:
        """Append the record unless its (run_id, call_id) exists. True if written."""
        try:
            with self._lock:
                if self._seen is None:
                    self._seen = self._load_seen()
                key = dedupe_key(record)
                if key in self._seen:
                    return False
                line = json.dumps(record, sort_keys=True, separators=(",", ":"))
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                self._seen.add(key)
                return True
        except Exception:  # capture must never break the online path
            self.dropped += 1
            logger.warning("capture write dropped", exc_info=True)
            return False
