"""Classify a worker's error response (pure; no I/O).

``slice_oom`` is the brief's stay-local case: the engine ran out of GPU memory on its slice. Sending
that request to another provider would hide a local capacity bug, so overflow never takes it
(``overflow.LOCAL_ONLY_REASONS``).
"""

from __future__ import annotations

import re

SLICE_OOM = "slice_oom"

_OOM = re.compile(
    r"out of memory|outofmemoryerror|cudaerrormemoryallocation|failed to allocate[^.]*memory",
    re.IGNORECASE,
)


def classify_error(status: int, body: str) -> str | None:
    """``slice_oom`` for a 5xx whose body reports a GPU out-of-memory failure, else None."""
    if status >= 500 and _OOM.search(body or ""):
        return SLICE_OOM
    return None
