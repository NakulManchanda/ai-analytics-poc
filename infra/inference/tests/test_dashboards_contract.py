"""Contract tests for the #115 slice E Grafana dashboard generator.

Verifies: the generator is deterministic, every PromQL metric name it uses exists in
the reference `.prom` fixtures (or an allowlisted set of kube/node/cAdvisor names),
and the JSON files committed on disk match the generator's current output.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GRAFANA_DIR = ROOT / "infra" / "inference" / "observability" / "grafana"
DASHBOARDS_MODULE = GRAFANA_DIR / "dashboards.py"
DASHBOARDS_DIR = GRAFANA_DIR / "dashboards"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Standard kube-state-metrics / node-exporter / cAdvisor names, matching what the
# previously committed cluster.json already used (see git history / class reference).
ALLOWED_NON_PROM_METRICS = frozenset(
    {
        "kube_node_status_condition",
        "kube_pod_status_phase",
        "kube_pod_container_status_restarts_total",
        "kube_deployment_status_replicas",
        "node_cpu_seconds_total",
        "node_memory_MemAvailable_bytes",
        "container_cpu_usage_seconds_total",
        "container_memory_working_set_bytes",
    }
)

METRIC_NAME_RE = re.compile(r"[A-Za-z_:][A-Za-z0-9_:]*")

# PromQL keywords/functions that the regex below will also match; excluded from the
# "must exist" check even if not immediately followed by "(" (belt-and-suspenders on
# top of the function-call and grouping-clause stripping below).
PROMQL_KEYWORDS = frozenset(
    {
        "sum",
        "by",
        "without",
        "avg",
        "min",
        "max",
        "count",
        "group",
        "on",
        "ignoring",
        "offset",
        "rate",
        "histogram_quantile",
        "clamp_min",
        "le",
        "or",
        "and",
    }
)

# Scientific-notation number literals (e.g. "1e-9") so the identifier regex doesn't
# pick up a stray "e" as a fake metric name.
SCI_NOTATION_RE = re.compile(r"\b\d+(?:\.\d+)?[eE]-?\d+\b")

# PromQL duration literals (e.g. "5m", "1h30m") used inside range-vector selectors
# ("[5m]") and `offset`/subquery clauses, so the identifier regex doesn't pick up
# their unit suffix (e.g. the "m" in "5m") as a fake metric name.
DURATION_RE = re.compile(r"\b\d+(?:ms|[smhdwy])\b")

# Label-matcher blocks, e.g. `{pod=~"...",container!=""}`.
LABEL_MATCHER_RE = re.compile(r"\{[^}]*\}")

# Aggregation grouping clauses, e.g. `by (le, instance)` / `without (pod)`, which
# contain label names, not metric names.
GROUPING_CLAUSE_RE = re.compile(r"\b(?:by|without)\s*\([^)]*\)")

# An identifier immediately followed by "(" (ignoring whitespace) is a PromQL
# function call, not a metric name.
FUNCTION_CALL_RE = re.compile(r"([A-Za-z_:][A-Za-z0-9_:]*)\s*\(")


def _extract_metric_identifiers(expr: str) -> set[str]:
    """Extract bare metric-name identifiers from a PromQL expression string.

    Excludes: string literals, label names inside `{...}` matchers, grouping-clause
    label lists (`by (...)`/`without (...)`), PromQL function names (identifiers
    immediately followed by `(`), and scientific-notation number literals.
    """
    text = SCI_NOTATION_RE.sub(" ", expr)
    text = DURATION_RE.sub(" ", text)
    text = LABEL_MATCHER_RE.sub(" ", text)
    text = GROUPING_CLAUSE_RE.sub(" ", text)
    function_names = set(FUNCTION_CALL_RE.findall(text))
    identifiers: set[str] = set()
    for token in METRIC_NAME_RE.findall(text):
        if token in function_names or token in PROMQL_KEYWORDS:
            continue
        identifiers.add(token)
    return identifiers


def _load_module():
    spec = importlib.util.spec_from_file_location("inference_dashboards", DASHBOARDS_MODULE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _fixture_metric_names() -> set[str]:
    names: set[str] = set()
    for fixture in ("vllm-worker-b.prom", "dcgm.prom"):
        for line in (FIXTURES / fixture).read_text(encoding="utf-8").splitlines():
            if line.startswith("# TYPE "):
                names.add(line.split()[2])
    return names


def test_fixtures_exist_and_are_redacted() -> None:
    for fixture in ("vllm-worker-b.prom", "dcgm.prom"):
        text = (FIXTURES / fixture).read_text(encoding="utf-8")
        assert text.strip()
        assert not re.search(r"(?:\d{1,3}\.){3}\d{1,3}", text), f"{fixture} contains an IP address"
        assert not re.search(r"\b[\w-]+\.(?:com|net|org|local|internal)\b", text), (
            f"{fixture} contains a hostname"
        )


def test_generator_is_deterministic() -> None:
    module = _load_module()
    first = {name: builder() for name, builder in module.DASHBOARDS.items()}
    second = {name: builder() for name, builder in module.DASHBOARDS.items()}
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def _base_names(names: set[str]) -> set[str]:
    # Histogram metrics expose derived series (_bucket, _sum, _count) that are not
    # themselves listed in the `# TYPE` lines of the .prom fixtures.
    return {re.sub(r"_(bucket|sum|count)$", "", name) for name in names}


def test_all_promql_metric_names_are_known() -> None:
    module = _load_module()
    known = _fixture_metric_names() | ALLOWED_NON_PROM_METRICS
    used: set[str] = set()
    for builder in module.DASHBOARDS.values():
        dashboard = builder()
        for panel in dashboard["panels"]:
            for target in panel.get("targets", []):
                expr = target.get("expr", "")
                used |= _extract_metric_identifiers(expr)
    base_names = _base_names(used)
    unknown = {name for name in base_names if name not in known}
    assert not unknown, f"Unknown/invented metric names used in dashboards: {sorted(unknown)}"
    # Sanity: we should have found a non-trivial number of real metric references.
    assert len(used) >= 10


def test_generator_metric_name_constant_is_known() -> None:
    """The generator's own METRIC_NAMES/ALLOWED_METRIC_NAMES list must not itself
    contain an invented metric name: it must be a subset of the fixtures ∪ the
    documented non-vLLM allowlist."""
    module = _load_module()
    known = _fixture_metric_names() | ALLOWED_NON_PROM_METRICS
    declared = _base_names(set(module.ALLOWED_METRIC_NAMES))
    unknown = {name for name in declared if name not in known}
    assert not unknown, f"Generator declares unknown/invented metric names: {sorted(unknown)}"


def test_committed_json_matches_generator_output(tmp_path: Path) -> None:
    module = _load_module()
    generated = module.write_dashboards(tmp_path)
    assert generated, "generator produced no dashboards"
    for path in generated:
        committed = DASHBOARDS_DIR / path.name
        assert committed.exists(), f"missing committed dashboard: {committed}"
        assert committed.read_text(encoding="utf-8") == path.read_text(encoding="utf-8"), (
            f"{committed} is stale; run `make inference-dashboards` and commit the result"
        )
    committed_names = {p.name for p in DASHBOARDS_DIR.glob("*.json")}
    generated_names = {p.name for p in generated}
    assert committed_names == generated_names, (
        "stray or missing dashboard JSON files: "
        f"committed-only={committed_names - generated_names}, "
        f"generated-only={generated_names - committed_names}"
    )
