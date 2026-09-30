"""Guard: reject work that should never reach a GPU (#122 slice 1)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

# Stable reason taxonomy (also the guard_reject_total{reason} label values).
MALFORMED_PAYLOAD = "malformed_payload"
MISSING_MESSAGES = "missing_messages"
PROMPT_TOO_LONG = "prompt_too_long"
GUARD_REASONS = (MALFORMED_PAYLOAD, MISSING_MESSAGES, PROMPT_TOO_LONG)


@dataclass(frozen=True)
class Guard:
    ok: bool
    code: str = "allow"
    reason: str = ""

    @property
    def http_status(self) -> int:
        return 200 if self.ok else (413 if self.code == PROMPT_TOO_LONG else 400)


def estimate_prompt_tokens(payload: dict, header_value: str | None = None) -> int:
    """Use the caller's x-estimated-prompt-tokens when valid, else ~4 chars/token."""
    try:
        if header_value is not None and int(header_value) >= 0:
            return int(header_value)
    except ValueError:
        pass
    return len(json.dumps(payload.get("messages", []))) // 4


def max_prompt_tokens() -> int:
    return int(os.getenv("MAX_PROMPT_TOKENS", "32768"))


def inspect(
    payload: object,
    *,
    estimated_tokens_header: str | None = None,
    max_tokens: int | None = None,
) -> Guard:
    if not isinstance(payload, dict):
        return Guard(False, MALFORMED_PAYLOAD, "payload must be a JSON object")
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return Guard(False, MISSING_MESSAGES, "messages must be a non-empty list")
    if not all(isinstance(m, dict) for m in messages):
        return Guard(False, MALFORMED_PAYLOAD, "each message must be an object")
    limit = max_prompt_tokens() if max_tokens is None else max_tokens
    tokens = estimate_prompt_tokens(payload, estimated_tokens_header)
    if tokens > limit:
        return Guard(False, PROMPT_TOO_LONG, f"estimated {tokens} tokens exceeds limit {limit}")
    return Guard(True)
