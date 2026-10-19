"""Append-only JSONL sink, one file per tenant. Never raises; failures are counted."""

from __future__ import annotations

import fcntl
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
        self._lock_path = self._path.parent / ".lock"
        self._seen: set[tuple[str, str]] = set()
        self._offset = 0
        self.dropped = 0

    def _refresh_seen(self) -> None:
        """Read records appended (by any process) since the last offset."""
        if not self._path.exists():
            return
        with self._path.open("rb") as fh:
            fh.seek(self._offset)
            data = fh.read()
        # only consume complete lines
        end = data.rfind(b"\n") + 1
        for line in data[:end].splitlines():
            try:
                self._seen.add(dedupe_key(json.loads(line)))
            except ValueError:
                continue
        self._offset += end

    def write(self, record: dict[str, Any]) -> bool:
        """Append the record unless its (run_id, call_id) exists. True if written.

        Thread-safe (lock) and multi-process-safe on POSIX (flock on a per-tenant
        lock file; existing keys are re-read under the lock)."""
        try:
            with self._lock:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._lock_path.open("a") as lock_fh:
                    fcntl.flock(lock_fh, fcntl.LOCK_EX)
                    try:
                        self._refresh_seen()
                        key = dedupe_key(record)
                        if key in self._seen:
                            return False
                        line = json.dumps(record, sort_keys=True, separators=(",", ":"))
                        with self._path.open("a", encoding="utf-8") as fh:
                            fh.write(line + "\n")
                        self._seen.add(key)
                        return True
                    finally:
                        fcntl.flock(lock_fh, fcntl.LOCK_UN)
        except Exception:  # capture must never break the online path
            self.dropped += 1
            logger.warning("capture write dropped", exc_info=True)
            return False
