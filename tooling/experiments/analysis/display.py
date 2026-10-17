"""Tiny table printer so notebooks and CI need no plotting/table dependency."""

from __future__ import annotations

from typing import Any


def flatten(d: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, key + "."))
        else:
            out[key] = v
    return out


def show(title: str, result: dict[str, Any]) -> None:
    """Print scope, warnings and the flattened scalar leaves of a result dict."""
    print(f"== {title}\n   scope: {result.get('scope', '')}")
    for w in result.get("warnings", []):
        print(f"   WARNING: {w}")
    for k, v in flatten(
        {k: v for k, v in result.items() if k not in ("scope", "warnings")}
    ).items():
        print(f"   {k}: {v}")
