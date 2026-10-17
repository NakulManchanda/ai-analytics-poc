"""Pure checks the serve-path smoke uses on the gateway's stage headers.

A response that merely arrives does not show that every stage ran; the gateway stamps one decision
header per stage (guard, admit, place, queue) and these helpers say what is missing or wrong.
"""

from __future__ import annotations

from collections.abc import Mapping

STAGE_HEADERS = (
    "x-guard-decision",
    "x-admit-decision",
    "x-place-decision",
    "x-queue-decision",
)


def _get(headers: Mapping[str, str], name: str) -> str:
    lowered = {key.lower(): value for key, value in headers.items()}
    return (lowered.get(name) or "").strip()


def missing_stage_headers(headers: Mapping[str, str]) -> list[str]:
    return [name for name in STAGE_HEADERS if not _get(headers, name)]


def problems_for_accepted(headers: Mapping[str, str]) -> list[str]:
    """What is wrong with the headers of a request that should have gone through every stage."""
    problems = [f"missing {name}" for name in missing_stage_headers(headers)]
    if (guard := _get(headers, "x-guard-decision")) and guard != "allow":
        problems.append(f"x-guard-decision is {guard!r}, expected 'allow'")
    if (admit := _get(headers, "x-admit-decision")) and admit != "accept":
        problems.append(f"x-admit-decision is {admit!r}, expected 'accept'")
    if (queue := _get(headers, "x-queue-decision")) and queue != "dispatched":
        problems.append(f"x-queue-decision is {queue!r}, expected 'dispatched'")
    return problems


def problems_for_rejected(
    status: int,
    headers: Mapping[str, str],
    *,
    expected_status: int,
    decision_prefix: str,
) -> list[str]:
    """What is wrong with a response that should have been rejected at a named stage."""
    problems = []
    if status != expected_status:
        problems.append(f"status {status}, expected {expected_status}")
    decision = _get(headers, "x-guard-decision") or _get(headers, "x-admit-decision")
    stamped = [
        _get(headers, name)
        for name in ("x-guard-decision", "x-admit-decision")
        if _get(headers, name).startswith(decision_prefix)
    ]
    if not stamped:
        problems.append(
            f"no decision header starts with {decision_prefix!r} (got {decision!r})"
        )
    return problems
