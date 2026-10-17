"""Guard: reject work that should never reach a GPU (#122 slice 1)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

# Stable reason taxonomy (also the guard_reject_total{reason} label values).
MALFORMED_PAYLOAD = "malformed_payload"
MISSING_MESSAGES = "missing_messages"
PROMPT_TOO_LONG = "prompt_too_long"
CONTEXT_TOO_LONG = "context_window_exceeded"
GUARD_REASONS = (MALFORMED_PAYLOAD, MISSING_MESSAGES, PROMPT_TOO_LONG, CONTEXT_TOO_LONG)


@dataclass(frozen=True)
class Guard:
    ok: bool
    code: str = "allow"
    reason: str = ""

    @property
    def http_status(self) -> int:
        if self.ok:
            return 200
        return 413 if self.code in (PROMPT_TOO_LONG, CONTEXT_TOO_LONG) else 400


def estimate_prompt_tokens(payload: dict, header_value: str | None = None) -> int:
    """~4 chars/token from the payload; a valid caller header can only raise the estimate.

    The header is caller-controlled, so it must never lower the gateway's own estimate
    (otherwise ``x-estimated-prompt-tokens: 0`` bypasses every limit built on this number).
    """
    own = len(json.dumps(payload.get("messages", []))) // 4
    try:
        if header_value is not None and int(header_value) >= 0:
            return max(own, int(header_value))
    except ValueError:
        pass
    return own


def requested_completion_tokens(payload: dict) -> int:
    """Completion budget the caller asked for (0 when absent or not a non-negative integer)."""
    for key in ("max_completion_tokens", "max_tokens"):
        value = payload.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return 0


def max_model_len() -> int | None:
    """The engine's --max-model-len; None (unset or 0) disables the context-window check."""
    value = int(os.getenv("MAX_MODEL_LEN", "0"))
    return value or None


def max_prompt_tokens() -> int:
    return int(os.getenv("MAX_PROMPT_TOKENS", "32768"))


def inspect(
    payload: object,
    *,
    estimated_tokens_header: str | None = None,
    max_tokens: int | None = None,
    model_len: int | None = None,
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
    window = max_model_len() if model_len is None else model_len
    completion = requested_completion_tokens(payload)
    if window is not None and tokens + completion > window:
        return Guard(
            False,
            CONTEXT_TOO_LONG,
            f"estimated prompt {tokens} + max_tokens {completion} exceeds context window {window}",
        )
    return Guard(True)
