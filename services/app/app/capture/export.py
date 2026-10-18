"""Offline JSONL export and purge for captured records."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from app.capture.schema import dedupe_key, sort_key
from app.capture.sink import tenant_file, validate_tenant


def _read(path: Path) -> list[dict]:
    records: dict[tuple[str, str], dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            records.setdefault(dedupe_key(rec), rec)
    return list(records.values())


def export_jsonl(root: Path, tenant_id: str, out_path: Path) -> int:
    """Write a deduplicated export sorted by (run_id, step); returns record count."""
    records = sorted(_read(tenant_file(root, tenant_id)), key=sort_key)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(
        "".join(
            json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in records
        ),
        encoding="utf-8",
    )
    os.replace(tmp, out)
    return len(records)


def purge(root: Path, tenant_id: str, run_id: str | None = None) -> int:
    """Delete one run's records, or the whole tenant directory. Returns records removed."""
    path = tenant_file(root, tenant_id)
    records = _read(path)
    if run_id is None:
        shutil.rmtree(Path(root) / validate_tenant(tenant_id), ignore_errors=True)
        return len(records)
    kept = [r for r in records if r.get("run_id") != run_id]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        "".join(
            json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in kept
        ),
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return len(records) - len(kept)
