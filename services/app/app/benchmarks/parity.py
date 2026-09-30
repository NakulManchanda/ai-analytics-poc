"""Parity gate for cross-arm comparisons (E6): reject missing/'unknown' metadata.

Used in two places:
- ``run_scenario.py --require-parity`` calls :func:`check_run` before any request.
- ``python -m app.benchmarks.parity <manifest.json> ...`` calls :func:`check_arms` on finished
  run manifests: every arm must be complete AND every shared input must be equal.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

NA = "n/a"
_UNKNOWN = {"", "unknown", "none", "null", "tbd", "?"}

# Required in every arm; must be equal across arms.
EQUAL_FIELDS = (
    "model_revision",
    "tokenizer_revision",
    "chat_template_revision",
    "engine_flags",
    "topology",
    "vllm_version",
    "kv_block_size",
    "max_tokens",
)


def _bad(value: Any) -> bool:
    return value is None or str(value).strip().lower() in _UNKNOWN


def _number(value: Any) -> bool:
    try:
        return int(str(value)) > 0 and not isinstance(value, bool)
    except ValueError:
        return False


def _is_dynamo(m: dict[str, Any]) -> bool:
    return m.get("router_label") not in (None, "gateway")


def check_run(m: dict[str, Any]) -> list[str]:
    """Completeness problems of one manifest (empty list means OK)."""
    p: list[str] = []
    for f in EQUAL_FIELDS:
        if _bad(m.get(f)) or (f == "vllm_version" and m.get(f) == NA):
            p.append(f"{f} missing/unknown")
    if not _bad(m.get("kv_block_size")) and not _number(m.get("kv_block_size")):
        p.append("kv_block_size must be a positive number")
    if _bad(m.get("scenario", {}).get("sha256")):
        p.append("scenario.sha256 missing")
    levels = m.get("offered_concurrency_levels")
    if not isinstance(levels, list) or not levels:
        p.append("offered_concurrency_levels missing or empty")
    if not m.get("slos"):
        p.append("slos missing")
    dv, db = m.get("dynamo_version"), m.get("dynamo_kv_block_size")
    if _bad(dv):
        p.append("dynamo_version missing (use 'n/a' explicitly for gateway arms)")
    if _bad(db):
        p.append("dynamo_kv_block_size missing (use 'n/a' explicitly for gateway arms)")
    if _is_dynamo(m):
        if dv == NA:
            p.append("dynamo_version must be concrete for a Dynamo arm")
        if not _number(db):
            p.append("dynamo_kv_block_size must be a positive number for a Dynamo arm")
        elif _number(m.get("kv_block_size")) and int(str(db)) != int(
            str(m["kv_block_size"])
        ):
            p.append(
                "dynamo_kv_block_size != kv_block_size (block hashes would differ)"
            )
    elif not _bad(db) and db != NA and not _number(db):
        p.append("dynamo_kv_block_size must be 'n/a' or a number")
    return p


REQUIRED_ROLES = ("A", "B", "C")
ROLE_DOC = {
    "A": "gateway + policy_override=least_loaded",
    "B": "gateway + policy_override=prefix_then_load",
    "C": "Dynamo router_label (dynamo*) with no policy_override",
}


def role_of(m: dict[str, Any]) -> str | None:
    """E6 arm role from router_label + execution.policy_override; None if ambiguous."""
    label = m.get("router_label") or (m.get("execution") or {}).get("router_label")
    override = (m.get("execution") or {}).get("policy_override")
    if label == "gateway":
        return {"least_loaded": "A", "prefix_then_load": "B"}.get(override)
    if isinstance(label, str) and label.startswith("dynamo") and override is None:
        return "C"
    return None


def check_arms(manifests: dict[str, dict[str, Any]]) -> list[str]:
    """Problems across arms: each arm complete, shared inputs equal, >= 2 arms."""
    problems = [f"{a}: {x}" for a, m in manifests.items() for x in check_run(m)]
    roles: dict[str, list[str]] = {r: [] for r in REQUIRED_ROLES}
    for arm, m in manifests.items():
        role = role_of(m)
        if role is None:
            ex = m.get("execution") or {}
            problems.append(
                f"{arm}: ambiguous arm role (router_label={m.get('router_label')!r}, "
                f"policy_override={ex.get('policy_override')!r}); expected one of "
                + "; ".join(f"{r}: {d}" for r, d in ROLE_DOC.items())
            )
        else:
            roles[role].append(arm)
    for r in REQUIRED_ROLES:
        if not roles[r]:
            problems.append(f"missing arm role {r} ({ROLE_DOC[r]})")
    fields = [(f, lambda m, f=f: m.get(f)) for f in EQUAL_FIELDS]
    fields += [
        ("scenario.sha256", lambda m: m.get("scenario", {}).get("sha256")),
        ("slos", lambda m: json.dumps(m.get("slos"), sort_keys=True)),
        (
            "offered_concurrency_levels",
            lambda m: str(m.get("offered_concurrency_levels")),
        ),
    ]
    for name, get in fields:
        # No silent filtering: a missing value is already reported by check_run and, for
        # levels, must still count as a difference.
        vals = {a: get(m) for a, m in manifests.items()}
        if len({str(v) for v in vals.values()}) > 1:
            problems.append(f"{name} differs across arms: {vals}")
    dyn = {a: m.get("dynamo_version") for a, m in manifests.items() if _is_dynamo(m)}
    if len(set(dyn.values())) > 1:
        problems.append(f"dynamo_version differs across Dynamo arms: {dyn}")
    return problems


def main(argv: list[str] | None = None) -> int:
    paths = [Path(a) for a in (argv if argv is not None else sys.argv[1:])]
    manifests = {str(p): json.loads(p.read_text(encoding="utf-8")) for p in paths}
    problems = check_arms(manifests)
    for line in problems:
        print(f"PARITY FAIL: {line}")
    if not problems:
        print(f"parity OK across {len(manifests)} arms")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
