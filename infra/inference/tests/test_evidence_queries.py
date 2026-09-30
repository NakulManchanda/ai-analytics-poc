"""The committed Prometheus range-export query list only uses allowlisted metric names."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
QUERIES = ROOT / "infra/inference/observability/prometheus/evidence_queries.json"
DASHBOARDS = ROOT / "infra/inference/observability/grafana/dashboards.py"
_TOKEN = re.compile(r"\b(?:vllm:|gateway_|orch_|DCGM_|worker_)[A-Za-z0-9_:]*")


def _allowed() -> frozenset[str]:
    spec = importlib.util.spec_from_file_location("dashboards_for_queries", DASHBOARDS)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod.ALLOWED_METRIC_NAMES


def _base(name: str) -> str:
    return re.sub(r"_(bucket|count|sum)$", "", name)


def test_every_query_metric_is_allowlisted_and_declared() -> None:
    allowed = _allowed()
    doc = json.loads(QUERIES.read_text(encoding="utf-8"))
    names = [q["name"] for q in doc["queries"]]
    assert len(names) == len(set(names))
    for q in doc["queries"]:
        used = {_base(t) for t in _TOKEN.findall(q["expr"])}
        assert used == set(q["metrics"]), q["name"]
        assert used <= allowed, f"{q['name']}: {used - allowed} not in dashboard allowlist"


def test_memory_proof_queries_are_present() -> None:
    from experiments.analysis.memory import PANELS

    names = {q["name"] for q in json.loads(QUERIES.read_text())["queries"]}
    assert set(PANELS) <= names
