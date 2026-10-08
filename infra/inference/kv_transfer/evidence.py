"""Fail-closed validation for the four-case KV transfer proof.

This module deliberately validates structured evidence rather than deriving a hop from latency,
placement headers, or a metadata-directory hit.  It has no dependency on LMCache, Mooncake, or
vLLM so retained run artifacts can be checked in a normal Python environment.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

CASE_LOCAL_REUSE = "same_worker_local_reuse"
CASE_RECOMPUTE = "cross_worker_recompute_transfer_disabled"
CASE_DESTINATION_HIT = "independently_warmed_destination_local_hit"
CASE_REAL_TRANSFER = "real_mooncake_transfer_consumed"

REQUIRED_CASES = (
    CASE_LOCAL_REUSE,
    CASE_RECOMPUTE,
    CASE_DESTINATION_HIT,
    CASE_REAL_TRANSFER,
)

_CASE_ALIASES = {
    CASE_LOCAL_REUSE: CASE_LOCAL_REUSE,
    "local_reuse": CASE_LOCAL_REUSE,
    CASE_RECOMPUTE: CASE_RECOMPUTE,
    "cross_worker_recompute": CASE_RECOMPUTE,
    CASE_DESTINATION_HIT: CASE_DESTINATION_HIT,
    "independent_destination_hit": CASE_DESTINATION_HIT,
    "destination_local_hit": CASE_DESTINATION_HIT,
    CASE_REAL_TRANSFER: CASE_REAL_TRANSFER,
    "real_mooncake_transfer": CASE_REAL_TRANSFER,
    "real_transfer": CASE_REAL_TRANSFER,
}

_EVENT_FIELDS = (
    "request_id",
    "conversation_id",
    "agent_step",
    "source_worker_or_store",
    "destination_worker",
    "compatibility_namespace",
    "hop_decision_reason",
    "hop_result",
    "reusable_tokens",
    "transferred_tokens",
    "transferred_bytes",
    "lookup_ms",
    "transfer_ms",
    "confirm_ms",
    "fallback_action",
    "consumed",
    "evidence_scope",
)

_VERSION_FIELDS = (
    "model_id",
    "model_revision",
    "tokenizer_revision",
    "chat_template_version",
    "prefix_contract_version",
    "weight_dtype",
    "kv_dtype",
    "adapter_namespace",
    "cache_namespace",
    "engine_version",
    "block_layout_version",
    "prefix_identity_version",
)

_CORRELATION_FIELDS = (
    "request_id",
    "conversation_id",
    "agent_step",
    "prefix_identity",
    "prefix_version",
    "destination_worker",
    "compatibility_namespace",
)


def _fail(reason: str) -> None:
    """Raise a short, stable reason without including raw high-cardinality values."""
    raise ValueError(reason[:180])


def _is_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _require_nonnegative_number(event: Mapping[str, Any], field: str) -> None:
    value = event[field]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"event field {field} must be a non-negative finite number")
    if not math.isfinite(value) or value < 0:
        _fail(f"event field {field} must be a non-negative finite number")


def _normalize_identity(event: Mapping[str, Any]) -> tuple[str, str]:
    """Return identity and schema version, accepting the issue's combined spelling."""
    identity = event.get("prefix_identity")
    version = event.get("prefix_version", event.get("prefix_identity_version"))
    combined = event.get("prefix_identity/version")

    if _is_text(identity) and _is_text(version):
        return str(identity), str(version)
    if _is_text(combined):
        combined_text = str(combined)
        if "@" in combined_text:
            parsed_identity, parsed_version = combined_text.rsplit("@", 1)
        elif "/" in combined_text:
            parsed_identity, parsed_version = combined_text.rsplit("/", 1)
        else:
            _fail("prefix_identity/version must include both identity and version")
        if _is_text(parsed_identity) and _is_text(parsed_version):
            return parsed_identity, parsed_version
    _fail("missing event fields: prefix_identity, prefix_version")


def _validate_event(raw_event: object) -> dict[str, Any]:
    if not isinstance(raw_event, Mapping):
        _fail("each evidence event must be a mapping")

    missing = [field for field in _EVENT_FIELDS if field not in raw_event]
    if missing:
        _fail("missing event fields: " + ", ".join(missing))

    event = dict(raw_event)
    identity, version = _normalize_identity(event)
    event["prefix_identity"] = identity
    event["prefix_version"] = version

    for field in (
        "request_id",
        "conversation_id",
        "source_worker_or_store",
        "destination_worker",
        "compatibility_namespace",
        "hop_decision_reason",
        "hop_result",
        "fallback_action",
    ):
        if not _is_text(event[field]):
            _fail(f"event field {field} must be a non-empty string")

    step = event["agent_step"]
    if isinstance(step, bool) or not isinstance(step, (int, str)):
        _fail("event field agent_step must be a non-empty string or non-negative integer")
    if isinstance(step, int) and step < 0:
        _fail("event field agent_step must be a non-empty string or non-negative integer")
    if isinstance(step, str) and not step.strip():
        _fail("event field agent_step must be a non-empty string or non-negative integer")

    for field in ("reusable_tokens", "transferred_tokens", "transferred_bytes"):
        value = event[field]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            _fail(f"event field {field} must be a non-negative integer")
    for field in ("lookup_ms", "transfer_ms", "confirm_ms"):
        _require_nonnegative_number(event, field)

    if not isinstance(event["consumed"], bool):
        _fail("event field consumed must be boolean")
    if event["evidence_scope"] not in {"per_request", "isolated_window"}:
        _fail("event field evidence_scope must be per_request or isolated_window")
    return event


def _case_name(case: object) -> str:
    if isinstance(case, str):
        candidate = case
    elif isinstance(case, Mapping):
        candidate = case.get("name", case.get("case"))
    else:
        _fail("case must be a name or mapping")
    if not _is_text(candidate):
        _fail("unknown controlled evidence case")
    base = str(candidate).split("@")[0] if "@" in str(candidate) else str(candidate)
    if base not in _CASE_ALIASES:
        _fail("unknown controlled evidence case")
    return _CASE_ALIASES[base]


def _validate_correlations(events: Sequence[Mapping[str, Any]]) -> None:
    expected = tuple(events[0][field] for field in _CORRELATION_FIELDS)
    for event in events[1:]:
        if tuple(event[field] for field in _CORRELATION_FIELDS) != expected:
            _fail("event correlation fields do not match")


def _require_no_transfer(event: Mapping[str, Any]) -> None:
    if event["transferred_tokens"] != 0 or event["transferred_bytes"] != 0:
        _fail("control case must record zero transferred tokens and bytes")


def _validate_local_reuse(event: Mapping[str, Any]) -> None:
    _require_no_transfer(event)
    if event["source_worker_or_store"] != event["destination_worker"]:
        _fail("local reuse source and destination must match")
    if event["hop_result"] not in {"local_reuse", "already_present", "present"}:
        _fail("local reuse must record a local result")
    if event["reusable_tokens"] <= 0:
        _fail("local reuse requires positive reusable tokens")
    if event["consumed"] is not True:
        _fail("local reuse requires destination consumption evidence")


def _validate_recompute(event: Mapping[str, Any]) -> None:
    _require_no_transfer(event)
    if event["source_worker_or_store"] == event["destination_worker"]:
        _fail("cross-worker recompute source and destination must differ")
    if event["hop_decision_reason"] != "transfer_disabled":
        _fail("recompute control must record transfer_disabled")
    if event["hop_result"] not in {"recompute", "recomputed"}:
        _fail("recompute control must record a recompute result")
    if event["fallback_action"] != "recompute":
        _fail("recompute control must record recompute fallback")
    if event["consumed"] is not False:
        _fail("recompute control cannot claim transferred-KV consumption")


def _validate_destination_hit(event: Mapping[str, Any]) -> None:
    _require_no_transfer(event)
    if event["source_worker_or_store"] != event["destination_worker"]:
        _fail("destination local hit source and destination must match")
    if event["hop_decision_reason"] not in {
        "independently_warmed_destination",
        "independent_destination_hit",
        "destination_local_hit",
    }:
        _fail("destination local hit must identify independent warming")
    if event["hop_result"] not in {
        "already_present",
        "destination_hit",
        "local_hit",
        "local_reuse",
    }:
        _fail("destination local hit must record a local-hit result")
    if event["reusable_tokens"] <= 0:
        _fail("destination local hit requires positive reusable tokens")
    if event["consumed"] is not True:
        _fail("destination local hit requires destination consumption evidence")


def _validate_real_transfer(event: Mapping[str, Any]) -> None:
    if event["hop_result"] != "transferred":
        _fail("real hop requires a transferred result")
    if event["source_worker_or_store"] == event["destination_worker"]:
        _fail("real hop source and destination must differ")
    if event["transferred_tokens"] <= 0:
        _fail("real hop requires positive transferred tokens")
    if event["transferred_bytes"] <= 0:
        _fail("real hop requires positive transferred bytes")
    if event["reusable_tokens"] <= 0:
        _fail("real hop requires positive reusable tokens")
    if event["transferred_tokens"] > event["reusable_tokens"]:
        _fail("transferred tokens cannot exceed reusable tokens")
    if event["consumed"] is not True:
        _fail("real hop requires destination consumption evidence")


_CASE_VALIDATORS = {
    CASE_LOCAL_REUSE: _validate_local_reuse,
    CASE_RECOMPUTE: _validate_recompute,
    CASE_DESTINATION_HIT: _validate_destination_hit,
    CASE_REAL_TRANSFER: _validate_real_transfer,
}


def validate_case(
    case: str | Mapping[str, Any], events: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Validate one controlled case and return a bounded summary.

    Every event is validated.  A fast transfer time or a worker header is ignored because neither
    proves that reusable KV was moved and consumed.
    """
    name = _case_name(case)
    if isinstance(events, (str, bytes)) or not isinstance(events, Sequence) or not events:
        _fail("case evidence must contain at least one event")

    normalized = [_validate_event(event) for event in events]
    _validate_correlations(normalized)
    scopes = {event["evidence_scope"] for event in normalized}
    if len(scopes) != 1:
        _fail("events cannot mix per_request and isolated_window evidence")

    validator = _CASE_VALIDATORS[name]
    for event in normalized:
        validator(event)
    raw_name = case.get("name", case.get("case", name)) if isinstance(case, Mapping) else str(case)
    return {"case": raw_name, "event_count": len(normalized), "evidence_scope": scopes.pop()}


def _manifest_versions(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    versions = manifest.get("versions")
    if not isinstance(versions, Mapping):
        _fail("manifest versions must be a mapping")
    missing = [field for field in _VERSION_FIELDS if not _is_text(versions.get(field))]
    if missing:
        _fail("manifest versions missing: " + ", ".join(missing))
    return versions


def _manifest_workers(manifest: Mapping[str, Any]) -> set[str]:
    topology = manifest.get("topology")
    if not isinstance(topology, Mapping) or not topology:
        _fail("manifest topology must be a non-empty mapping")
    raw_workers = topology.get("workers")
    if raw_workers is None and ("worker_a" in topology and "worker_b" in topology):
        raw_workers = ["worker_a", "worker_b"]
    if isinstance(raw_workers, (str, bytes)) or not isinstance(raw_workers, Sequence):
        _fail("manifest topology must list at least two workers")

    workers: set[str] = set()
    for raw_worker in raw_workers:
        if _is_text(raw_worker):
            workers.add(str(raw_worker))
        elif isinstance(raw_worker, Mapping):
            worker_id = raw_worker.get("id", raw_worker.get("name"))
            if _is_text(worker_id):
                workers.add(str(worker_id))
    if len(workers) < 2:
        _fail("manifest topology must list at least two workers")
    return workers


def _iter_cases(cases: object) -> list[tuple[object, object]]:
    if isinstance(cases, Mapping):
        entries: list[tuple[object, object]] = []
        for key, value in cases.items():
            if isinstance(value, Mapping):
                case = dict(value)
                case.setdefault("name", key)
                entries.append((case, case.get("events")))
            else:
                entries.append(({"name": key}, value))
        return entries
    if isinstance(cases, (str, bytes)) or not isinstance(cases, Sequence):
        _fail("cases must be a mapping or sequence")
    entries = []
    for case in cases:
        if not isinstance(case, Mapping):
            _fail("each run case must be a mapping")
        entries.append((case, case.get("events")))
    return entries


def validate_run(manifest: Mapping[str, Any], cases: object) -> dict[str, Any]:
    """Validate provenance and exactly one instance of every controlled proof case."""
    if not isinstance(manifest, Mapping):
        _fail("manifest must be a mapping")
    workers = _manifest_workers(manifest)
    versions = _manifest_versions(manifest)
    namespace = manifest.get("compatibility_namespace")
    if not _is_text(namespace):
        _fail("manifest compatibility namespace must be non-empty")
    if not _is_text(manifest.get("control_isolation_statement")):
        _fail("manifest control isolation statement must be non-empty")

    entries = _iter_cases(cases)
    seen: list[str] = []
    cases_by_size: dict[str, set[str]] = {}
    has_size_tags = False

    for case, raw_events in entries:
        raw_name = case.get("name") if isinstance(case, Mapping) else str(case)
        if not _is_text(raw_name):
            _fail("case name must be non-empty string")
        if raw_name in seen:
            _fail("duplicate case in evidence run")
        seen.append(raw_name)

        if "@" in raw_name:
            has_size_tags = True
            base_name, size_tag = raw_name.split("@", 1)
        else:
            base_name, size_tag = raw_name, ""

        name = _case_name(base_name)
        cases_by_size.setdefault(size_tag, set()).add(name)

        validate_case(case, raw_events)

        # Repeat the small normalization pass to check the evidence against run provenance.
        normalized = [_validate_event(event) for event in raw_events]
        for event in normalized:
            if event["destination_worker"] not in workers:
                _fail("event destination is absent from manifest topology")
            if event["compatibility_namespace"] != namespace:
                _fail("event compatibility namespace does not match manifest")
            if event["prefix_version"] != versions["prefix_identity_version"]:
                _fail("event prefix identity version does not match manifest")

    if has_size_tags:
        for size_tag, group_cases in cases_by_size.items():
            if set(group_cases) != set(REQUIRED_CASES) or len(group_cases) != len(REQUIRED_CASES):
                _fail(f"run prefix size {size_tag} must contain exactly the four controlled cases")
    else:
        if set(seen) != set(REQUIRED_CASES) or len(seen) != len(REQUIRED_CASES):
            _fail("run must contain exactly the four controlled cases")
    return {"valid": True, "case_count": len(seen), "cases": seen}


__all__ = [
    "CASE_DESTINATION_HIT",
    "CASE_LOCAL_REUSE",
    "CASE_REAL_TRANSFER",
    "CASE_RECOMPUTE",
    "REQUIRED_CASES",
    "validate_case",
    "validate_run",
]
