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

import yaml

from infra.inference.gateway import metrics as gateway_metrics

ROOT = Path(__file__).resolve().parents[3]
GRAFANA_DIR = ROOT / "infra" / "inference" / "observability" / "grafana"
DASHBOARDS_MODULE = GRAFANA_DIR / "dashboards.py"
DASHBOARDS_DIR = GRAFANA_DIR / "dashboards"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
PROM_DIR = ROOT / "infra" / "inference" / "observability" / "prometheus"
ALERTS_FILE = PROM_DIR / "alerts.yaml"

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
        "up",
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
        "unless",
        "bool",
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
    spec = importlib.util.spec_from_file_location(
        "inference_dashboards", DASHBOARDS_MODULE
    )
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


def _gateway_metric_names() -> set[str]:
    """Series base names the gateway registry exports (labelled metrics have no samples until
    used, so derive from the families: counters expose `<name>_total`)."""
    names: set[str] = set()
    for family in gateway_metrics.REGISTRY.collect():
        names |= {family.name, f"{family.name}_total"}
    return names


def _known_names() -> set[str]:
    return _fixture_metric_names() | ALLOWED_NON_PROM_METRICS | _gateway_metric_names()


def test_fixtures_exist_and_are_redacted() -> None:
    for fixture in ("vllm-worker-b.prom", "dcgm.prom"):
        text = (FIXTURES / fixture).read_text(encoding="utf-8")
        assert text.strip()
        assert not re.search(
            r"(?:\d{1,3}\.){3}\d{1,3}", text
        ), f"{fixture} contains an IP address"
        assert not re.search(
            r"\b[\w-]+\.(?:com|net|org|local|internal)\b", text
        ), f"{fixture} contains a hostname"


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
    known = _base_names(_known_names())
    used: set[str] = set()
    for builder in module.DASHBOARDS.values():
        dashboard = builder()
        for panel in dashboard["panels"]:
            for target in panel.get("targets", []):
                expr = target.get("expr", "")
                used |= _extract_metric_identifiers(expr)
    base_names = _base_names(used)
    unknown = {name for name in base_names if name not in known}
    assert (
        not unknown
    ), f"Unknown/invented metric names used in dashboards: {sorted(unknown)}"
    # Sanity: we should have found a non-trivial number of real metric references.
    assert len(used) >= 10


def test_generator_metric_name_constant_is_known() -> None:
    """The generator's own METRIC_NAMES/ALLOWED_METRIC_NAMES list must not itself
    contain an invented metric name: it must be a subset of the fixtures ∪ the
    documented non-vLLM allowlist."""
    module = _load_module()
    known = _base_names(_known_names())
    declared = _base_names(set(module.ALLOWED_METRIC_NAMES))
    unknown = {name for name in declared if name not in known}
    assert (
        not unknown
    ), f"Generator declares unknown/invented metric names: {sorted(unknown)}"


def test_committed_json_matches_generator_output(tmp_path: Path) -> None:
    module = _load_module()
    generated = module.write_dashboards(tmp_path)
    assert generated, "generator produced no dashboards"
    for path in generated:
        committed = DASHBOARDS_DIR / path.name
        assert committed.exists(), f"missing committed dashboard: {committed}"
        assert committed.read_text(encoding="utf-8") == path.read_text(
            encoding="utf-8"
        ), f"{committed} is stale; run `make inference-dashboards` and commit the result"
    committed_names = {p.name for p in DASHBOARDS_DIR.glob("*.json")}
    generated_names = {p.name for p in generated}
    assert committed_names == generated_names, (
        "stray or missing dashboard JSON files: "
        f"committed-only={committed_names - generated_names}, "
        f"generated-only={generated_names - committed_names}"
    )


def test_gateway_group_is_declared_and_subset_of_gateway_registry() -> None:
    module = _load_module()
    group = set(module.METRIC_NAMES["gateway"])
    assert group, "generator must declare a 'gateway' metric group"
    assert _base_names(group) <= _base_names(_gateway_metric_names())


def test_every_panel_has_a_target_and_unique_ids() -> None:
    module = _load_module()
    for name, builder in module.DASHBOARDS.items():
        panels = builder()["panels"]
        ids = [p["id"] for p in panels]
        assert len(ids) == len(set(ids)), f"{name}: duplicate panel ids"
        for panel in panels:
            if panel["type"] == "text":
                continue
            assert panel["targets"], f"{name}: panel {panel['title']!r} has no target"
            assert all(t["expr"].strip() for t in panel["targets"])


def test_slice_b_dashboards_exist_and_cover_gateway_metrics() -> None:
    module = _load_module()
    expected = {
        "overview",
        "gateway_admission",
        "router_placement",
        "queues",
        "overflow",
        "memory_proof",
        "kv_hop_stub",
    }
    assert expected <= set(module.DASHBOARDS)
    used: set[str] = set()
    for name in expected - {"kv_hop_stub"}:
        for panel in module.DASHBOARDS[name]()["panels"]:
            for target in panel.get("targets", []):
                used |= _extract_metric_identifiers(target["expr"])
    for metric in (
        "gateway_requests_total",
        "orch_admit_total",
        "orch_pick_total",
        "orch_replica_queue_depth",
        "orch_queue_wait_seconds_bucket",
        "orch_overflow_total",
        "DCGM_FI_DEV_FB_USED",
        "vllm:kv_cache_usage_perc",
    ):
        assert metric in used, f"no slice-B panel references {metric}"
    stub = module.DASHBOARDS["kv_hop_stub"]()
    assert all(p["type"] == "text" for p in stub["panels"])
    assert "#133" in json.dumps(stub)


def _alert_rules() -> list[dict]:
    doc = yaml.safe_load(ALERTS_FILE.read_text(encoding="utf-8"))
    return [rule for group in doc["groups"] for rule in group["rules"]]


def test_alert_rules_structure_and_metrics_are_known() -> None:
    rules = _alert_rules()
    assert len(rules) == 5
    known = _base_names(_known_names())
    for rule in rules:
        assert rule["alert"] and rule["expr"] and rule["for"]
        assert rule["labels"]["severity"]
        assert rule["annotations"]["summary"]
        unknown = {
            n
            for n in _base_names(_extract_metric_identifiers(rule["expr"]))
            if n not in known
        }
        assert not unknown, f"{rule['alert']} uses unknown metrics: {sorted(unknown)}"
    assert {r["alert"] for r in rules} == {
        "InferenceKVPressureSustained",
        "GatewayInteractiveTTFTSLOBreach",
        "InferenceEngineTTFTHigh",
        "GatewayQueueShedSurge",
        "InferenceWorkerIntegrity",
    }


def test_prometheus_scrapes_gateway_and_loads_alert_rules() -> None:
    values = yaml.safe_load((PROM_DIR / "values.yaml").read_text(encoding="utf-8"))
    jobs = {j["job_name"]: j for j in yaml.safe_load(values["extraScrapeConfigs"])}
    targets = jobs["inference-gateway"]["static_configs"][0]["targets"]
    assert targets == ["inference-gateway.inference-lab.svc.cluster.local:8080"]
    assert values["alertmanager"]["enabled"] is False
    deploy = (ROOT / "infra" / "inference" / "scripts" / "deploy.sh").read_text(
        encoding="utf-8"
    )
    assert "observability/prometheus/alerts.yaml" in deploy


def test_alert_expressions_cover_target_loss_and_gateway_ttft() -> None:
    by_name = {r["alert"]: " ".join(r["expr"].split()) for r in _alert_rules()}
    integrity = by_name["InferenceWorkerIntegrity"]
    assert (
        'up{job=~"inference-gateway|inference-workers"} == 0' in integrity
    )  # target down
    assert 'absent(up{job="inference-gateway"})' in integrity  # gateway series vanished
    assert (
        'count(up{job="inference-workers"} == 1) or vector(0)) < 2' in integrity
    )  # worker gone
    slo = by_name["GatewayInteractiveTTFTSLOBreach"]
    assert 'gateway_ttft_seconds_bucket{class="interactive"}' in slo and "> 0.1" in slo
    assert "vllm:" not in slo
    assert "vllm:time_to_first_token_seconds" in by_name["InferenceEngineTTFTHigh"]


def test_ttft_and_warm_panels_exist() -> None:
    module = _load_module()
    text = json.dumps({k: b() for k, b in module.DASHBOARDS.items()})
    assert "gateway_ttft_seconds_bucket" in text and "worker_warm" in text
