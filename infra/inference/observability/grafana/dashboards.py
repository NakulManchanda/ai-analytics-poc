"""Deterministic Grafana dashboard generator for the isolated inference lab (#115 slice E).

Mirrors the style of the class reference generator
(``.vscode/myfiles/115-react-workload/experiments/class_dashboards_reference.py``):
``_target``/``_panel``/``_dash``/``write_dashboards``. Unlike that reference, every
metric name used here is one this lab actually exposes today (see
``infra/inference/tests/fixtures/vllm-worker-b.prom`` and ``dcgm.prom``), plus the
standard kube-state-metrics / node-exporter / cAdvisor names already used by the
committed ``cluster.json``. Since #123 slice B the gateway's own ``/metrics`` (scraped as
job ``inference-gateway``, see ``infra/inference/gateway/metrics.py``) backs the ``orch_*``
dashboards. No KEDA, HAMi allocator, or Mooncake/KV-hop panels are included: the real hop
metrics arrive with #133 (``kv_hop_stub`` is a text-only placeholder).

Worker-distinguishing label: Prometheus scrapes the two workers as two separate
``static_configs`` targets (see ``infra/inference/observability/prometheus/values.yaml``):
``inference-worker-a.inference-lab.svc.cluster.local:8000`` and
``inference-worker-b...:8000``. There is no ``pod``/``app``/``job`` label split per
worker in these series (no Prometheus pod service-discovery relabeling is configured),
so ``instance`` is the only label that distinguishes worker A from worker B in the
vLLM metrics. Kube-state-metrics and cAdvisor series (pods, containers) are labelled
by ``pod``/``namespace`` as usual and are split by ``pod`` instead. DCGM is scraped as
a single static target for the shared GPU (see
``infra/inference/observability/prometheus/values.yaml``), so its series have no
``pod`` label at all and are split by ``instance`` (the DCGM exporter's scrape
target), not by ``pod``.

Regenerate with ``python3 infra/inference/observability/grafana/dashboards.py`` (or
``make inference-dashboards``) and commit the resulting JSON.
"""

from __future__ import annotations

import json
from pathlib import Path

PROM = {"type": "prometheus", "uid": "prometheus"}

# From infra/inference/k8s/workers/worker-a.yaml and worker-b.yaml.
MAX_NUM_SEQS = 8
WORKER_POD_MATCH = 'pod=~"inference-worker-a.*|inference-worker-b.*"'

# Metric names this generator is allowed to reference, grouped for the contract test.
# vLLM names come from infra/inference/tests/fixtures/vllm-worker-b.prom (v0.11.0,
# Qwen3-0.6B). DCGM names come from infra/inference/tests/fixtures/dcgm.prom. The
# kube/node/cAdvisor allowlist matches names already used in the committed cluster.json.
METRIC_NAMES = {
    "vllm": (
        "vllm:kv_cache_usage_perc",
        "vllm:prefix_cache_hits_total",
        "vllm:prefix_cache_queries_total",
        "vllm:num_preemptions_total",
        "vllm:cache_config_info",
        "vllm:request_prefill_time_seconds",
        "vllm:request_decode_time_seconds",
        "vllm:request_queue_time_seconds",
        "vllm:request_inference_time_seconds",
        "vllm:time_to_first_token_seconds",
        "vllm:time_per_output_token_seconds",
        "vllm:prompt_tokens_total",
        "vllm:generation_tokens_total",
        "vllm:iteration_tokens_total",
        "vllm:request_prompt_tokens",
        "vllm:request_generation_tokens",
        "vllm:num_requests_running",
        "vllm:num_requests_waiting",
        "vllm:e2e_request_latency_seconds",
        "vllm:request_success_total",
    ),
    "gateway": (
        "gateway_requests_total",
        "gateway_request_duration_seconds",
        "gateway_ttft_seconds",
        "worker_warm",
        "guard_reject_total",
        "orch_pick_total",
        "placement_error_total",
        "worker_health",
        "worker_snapshot_age_seconds",
        "stale_snapshot_fallback_total",
        "orch_admit_total",
        "orch_shed_total",
        "orch_tenant_total",
        "orch_replica_queue_depth",
        "orch_queue_wait_seconds",
        "queue_error_total",
        "orch_overflow_total",
        "overflow_error_total",
    ),
    "dcgm": (
        "DCGM_FI_DEV_GPU_UTIL",
        "DCGM_FI_DEV_FB_USED",
        "DCGM_FI_DEV_FB_FREE",
        "DCGM_FI_DEV_POWER_USAGE",
        "DCGM_FI_DEV_MEM_COPY_UTIL",
    ),
    "kube_node_cadvisor": (
        "kube_node_status_condition",
        "kube_pod_status_phase",
        "kube_pod_container_status_restarts_total",
        "kube_deployment_status_replicas",
        "node_cpu_seconds_total",
        "node_memory_MemAvailable_bytes",
        "container_cpu_usage_seconds_total",
        "container_memory_working_set_bytes",
        "up",
    ),
}

ALLOWED_METRIC_NAMES = frozenset(
    name for group in METRIC_NAMES.values() for name in group
)

UP_THRESHOLDS = {
    "mode": "absolute",
    "steps": [
        {"color": "red", "value": None},
        {"color": "green", "value": 1.0},
    ],
}


def _target(
    expr: str,
    legend: str = "",
    ref: str = "A",
    *,
    instant: bool = False,
    fmt: str = "",
) -> dict:
    target: dict = {"datasource": PROM, "expr": expr, "refId": ref}
    if legend:
        target["legendFormat"] = legend
    if instant:
        target["instant"] = True
    if fmt:
        target["format"] = fmt
    return target


def _panel(
    pid: int,
    title: str,
    expr: str,
    *,
    legend: str = "",
    kind: str = "timeseries",
    x: int,
    y: int,
    w: int = 12,
    h: int = 8,
    extra: list[tuple[str, str]] | None = None,
    unit: str = "",
    thresholds: dict | None = None,
) -> dict:
    is_table = kind == "table"
    targets = [
        _target(expr, legend, "A", instant=is_table, fmt="table" if is_table else "")
    ]
    for i, (ex, leg) in enumerate(extra or []):
        targets.append(
            _target(
                ex,
                leg,
                chr(ord("B") + i),
                instant=is_table,
                fmt="table" if is_table else "",
            )
        )
    defaults: dict = {
        "custom": {"drawStyle": "line", "fillOpacity": 10, "lineWidth": 1}
    }
    if unit:
        defaults["unit"] = unit
    if thresholds:
        defaults["thresholds"] = thresholds
    panel = {
        "id": pid,
        "type": kind,
        "title": title,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "datasource": PROM,
        "targets": targets,
        "options": {
            "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True}
        },
        "fieldConfig": {"defaults": defaults, "overrides": []},
    }
    if kind == "stat":
        panel["options"] = {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "colorMode": "value",
            "graphMode": "area",
            "justifyMode": "auto",
            "textMode": "auto",
        }
    elif kind == "table":
        panel["options"] = {"showHeader": True}
    return panel


def _text_panel(
    pid: int, title: str, body: str, *, x: int, y: int, w: int = 24, h: int = 3
) -> dict:
    return {
        "id": pid,
        "type": "text",
        "title": title,
        "gridPos": {"h": h, "w": w, "x": x, "y": y},
        "options": {"mode": "markdown", "content": body},
        "fieldConfig": {"defaults": {}, "overrides": []},
    }


def _dash(uid: str, title: str, panels: list[dict], tags: list[str]) -> dict:
    return {
        "uid": uid,
        "title": title,
        "tags": tags,
        "schemaVersion": 39,
        "version": 2,
        "timezone": "browser",
        "refresh": "5s",
        "time": {"from": "now-1h", "to": "now"},
        "templating": {"list": []},
        "annotations": {"list": []},
        "editable": True,
        "fiscalYearStartMonth": 0,
        "graphTooltip": 1,
        "links": [],
        "liveNow": True,
        "panels": panels,
        "style": "dark",
    }


def kv_prefix_cache() -> dict:
    note = _text_panel(
        1,
        "How to read this",
        (
            "KV usage % near 100 with rising queue time means the KV cache, not "
            "`max_num_seqs`, is the binding constraint. A low prefix-cache hit "
            "ratio despite repeated prompts means prefix caching isn't matching "
            "(check `enable_prefix_caching` in the cache-config table below). "
            "Preemptions/s > 0 mean requests are being evicted mid-generation to "
            "free KV blocks for new arrivals."
        ),
        x=0,
        y=0,
    )
    panels = [
        note,
        _panel(
            2,
            "KV cache usage % per worker",
            "vllm:kv_cache_usage_perc",
            legend="{{instance}}",
            x=0,
            y=3,
            w=12,
            h=8,
            unit="percentunit",
        ),
        _panel(
            3,
            "Prefix cache hit ratio per worker",
            "rate(vllm:prefix_cache_hits_total[5m]) / clamp_min(rate(vllm:prefix_cache_queries_total[5m]), 1e-9)",
            legend="{{instance}}",
            x=12,
            y=3,
            w=12,
            h=8,
            unit="percentunit",
        ),
        _panel(
            4,
            "Prefix cache hits/s per worker",
            "rate(vllm:prefix_cache_hits_total[5m])",
            legend="{{instance}}",
            x=0,
            y=11,
            w=8,
            h=8,
        ),
        _panel(
            5,
            "Prefix cache queries/s per worker",
            "rate(vllm:prefix_cache_queries_total[5m])",
            legend="{{instance}}",
            x=8,
            y=11,
            w=8,
            h=8,
        ),
        _panel(
            6,
            "Preemptions/s per worker",
            "rate(vllm:num_preemptions_total[5m])",
            legend="{{instance}}",
            x=16,
            y=11,
            w=8,
            h=8,
        ),
        _panel(
            7,
            "Cache config per worker",
            "vllm:cache_config_info",
            legend="{{instance}} block_size={{block_size}} num_gpu_blocks={{num_gpu_blocks}}",
            x=0,
            y=19,
            w=24,
            h=6,
            kind="table",
        ),
    ]
    return _dash(
        "inference-kv-prefix-cache",
        "Inference Lab / KV & Prefix Cache",
        panels,
        ["inference-lab", "vllm", "kv-cache"],
    )


def prefill_decode() -> dict:
    note = _text_panel(
        1,
        "How to read this",
        (
            "Prefill time scales with prompt length; decode time scales with "
            "generation length and step count. A large p95 `request_queue_time`"
            " next to small prefill/decode times means requests are waiting for "
            "a free slot, not for GPU compute. Large `iteration_tokens_total` "
            "values mean an engine step processed a prefill-heavy batch; small "
            "values mean a decode-only (token-at-a-time) step. Caution: vLLM's "
            "time histograms start at 0.3 s (ITL: 10 ms), so for fast requests "
            "p50/p95 are interpolated inside the first bucket (about 150/285 ms) "
            "and say only \"under 0.3 s\"; read the mean series instead. TTFT "
            "has fine buckets and is reliable."
        ),
        x=0,
        y=0,
    )
    panels = [
        note,
        _panel(
            2,
            "Prefill time p50/p95/mean per worker",
            "histogram_quantile(0.50, sum by (le, instance) (rate(vllm:request_prefill_time_seconds_bucket[5m])))",
            legend="p50 {{instance}}",
            extra=[
                (
                    "histogram_quantile(0.95, sum by (le, instance) (rate(vllm:request_prefill_time_seconds_bucket[5m])))",
                    "p95 {{instance}}",
                ),
                (
                    "sum by (instance) (rate(vllm:request_prefill_time_seconds_sum[5m])) / "
                    "clamp_min(sum by (instance) (rate(vllm:request_prefill_time_seconds_count[5m])), 1e-9)",
                    "mean {{instance}}",
                ),
            ],
            x=0,
            y=3,
            w=12,
            h=8,
            unit="s",
        ),
        _panel(
            3,
            "Decode time p50/p95/mean per worker",
            "histogram_quantile(0.50, sum by (le, instance) (rate(vllm:request_decode_time_seconds_bucket[5m])))",
            legend="p50 {{instance}}",
            extra=[
                (
                    "histogram_quantile(0.95, sum by (le, instance) (rate(vllm:request_decode_time_seconds_bucket[5m])))",
                    "p95 {{instance}}",
                ),
                (
                    "sum by (instance) (rate(vllm:request_decode_time_seconds_sum[5m])) / "
                    "clamp_min(sum by (instance) (rate(vllm:request_decode_time_seconds_count[5m])), 1e-9)",
                    "mean {{instance}}",
                ),
            ],
            x=12,
            y=3,
            w=12,
            h=8,
            unit="s",
        ),
        _panel(
            4,
            "Queue time p50/p95/mean per worker",
            "histogram_quantile(0.50, sum by (le, instance) (rate(vllm:request_queue_time_seconds_bucket[5m])))",
            legend="p50 {{instance}}",
            extra=[
                (
                    "histogram_quantile(0.95, sum by (le, instance) (rate(vllm:request_queue_time_seconds_bucket[5m])))",
                    "p95 {{instance}}",
                ),
                (
                    "sum by (instance) (rate(vllm:request_queue_time_seconds_sum[5m])) / "
                    "clamp_min(sum by (instance) (rate(vllm:request_queue_time_seconds_count[5m])), 1e-9)",
                    "mean {{instance}}",
                ),
            ],
            x=0,
            y=11,
            w=12,
            h=8,
            unit="s",
        ),
        _panel(
            5,
            "Inference time p50/p95/mean per worker",
            "histogram_quantile(0.50, sum by (le, instance) (rate(vllm:request_inference_time_seconds_bucket[5m])))",
            legend="p50 {{instance}}",
            extra=[
                (
                    "histogram_quantile(0.95, sum by (le, instance) (rate(vllm:request_inference_time_seconds_bucket[5m])))",
                    "p95 {{instance}}",
                ),
                (
                    "sum by (instance) (rate(vllm:request_inference_time_seconds_sum[5m])) / "
                    "clamp_min(sum by (instance) (rate(vllm:request_inference_time_seconds_count[5m])), 1e-9)",
                    "mean {{instance}}",
                ),
            ],
            x=12,
            y=11,
            w=12,
            h=8,
            unit="s",
        ),
        _panel(
            6,
            "TTFT p50/p95 per worker",
            "histogram_quantile(0.50, sum by (le, instance) (rate(vllm:time_to_first_token_seconds_bucket[5m])))",
            legend="p50 {{instance}}",
            extra=[
                (
                    "histogram_quantile(0.95, sum by (le, instance) (rate(vllm:time_to_first_token_seconds_bucket[5m])))",
                    "p95 {{instance}}",
                )
            ],
            x=0,
            y=19,
            w=12,
            h=8,
            unit="s",
        ),
        _panel(
            7,
            "ITL (time per output token) p50/p95/mean per worker",
            "histogram_quantile(0.50, sum by (le, instance) (rate(vllm:time_per_output_token_seconds_bucket[5m])))",
            legend="p50 {{instance}}",
            extra=[
                (
                    "histogram_quantile(0.95, sum by (le, instance) (rate(vllm:time_per_output_token_seconds_bucket[5m])))",
                    "p95 {{instance}}",
                ),
                (
                    "sum by (instance) (rate(vllm:time_per_output_token_seconds_sum[5m])) / "
                    "clamp_min(sum by (instance) (rate(vllm:time_per_output_token_seconds_count[5m])), 1e-9)",
                    "mean {{instance}}",
                ),
            ],
            x=12,
            y=19,
            w=12,
            h=8,
            unit="s",
        ),
        _panel(
            8,
            "Prompt tokens/s vs generation tokens/s per worker",
            "rate(vllm:prompt_tokens_total[5m])",
            legend="prompt {{instance}}",
            extra=[
                ("rate(vllm:generation_tokens_total[5m])", "generation {{instance}}")
            ],
            x=0,
            y=27,
            w=12,
            h=8,
        ),
        _panel(
            9,
            "Iteration tokens per step (p50/p95) per worker",
            "histogram_quantile(0.50, sum by (le, instance) (rate(vllm:iteration_tokens_total_bucket[5m])))",
            legend="p50 {{instance}}",
            extra=[
                (
                    "histogram_quantile(0.95, sum by (le, instance) (rate(vllm:iteration_tokens_total_bucket[5m])))",
                    "p95 {{instance}}",
                )
            ],
            x=12,
            y=27,
            w=12,
            h=8,
        ),
        _panel(
            10,
            "Prompt tokens per request (p50/p95) per worker",
            "histogram_quantile(0.50, sum by (le, instance) (rate(vllm:request_prompt_tokens_bucket[5m])))",
            legend="p50 {{instance}}",
            extra=[
                (
                    "histogram_quantile(0.95, sum by (le, instance) (rate(vllm:request_prompt_tokens_bucket[5m])))",
                    "p95 {{instance}}",
                )
            ],
            x=0,
            y=35,
            w=12,
            h=8,
        ),
        _panel(
            11,
            "Generation tokens per request (p50/p95) per worker",
            "histogram_quantile(0.50, sum by (le, instance) (rate(vllm:request_generation_tokens_bucket[5m])))",
            legend="p50 {{instance}}",
            extra=[
                (
                    "histogram_quantile(0.95, sum by (le, instance) (rate(vllm:request_generation_tokens_bucket[5m])))",
                    "p95 {{instance}}",
                )
            ],
            x=12,
            y=35,
            w=12,
            h=8,
        ),
        _panel(
            12,
            "Prefill share of prefill+decode time per worker",
            (
                "sum by (instance) (rate(vllm:request_prefill_time_seconds_sum[5m])) / "
                "clamp_min(sum by (instance) (rate(vllm:request_prefill_time_seconds_sum[5m]) + "
                "rate(vllm:request_decode_time_seconds_sum[5m])), 1e-9)"
            ),
            legend="{{instance}}",
            x=0,
            y=43,
            w=24,
            h=8,
            unit="percentunit",
        ),
    ]
    return _dash(
        "inference-prefill-decode",
        "Inference Lab / Prefill vs Decode",
        panels,
        ["inference-lab", "vllm", "prefill-decode"],
    )


def scheduler_concurrency() -> dict:
    note = _text_panel(
        1,
        "How to read this",
        (
            f"`running / {MAX_NUM_SEQS}` (the configured `max_num_seqs`) is a "
            "slot-saturation line: as it approaches 1.0 the scheduler is admitting "
            "as many sequences as it is allowed to, regardless of remaining KV "
            "headroom. A rising `waiting` count with `running` pinned at the max "
            "means new requests are queueing behind the slot limit, not the KV cache."
        ),
        x=0,
        y=0,
    )
    panels = [
        note,
        _panel(
            2,
            "Running vs waiting per worker",
            "vllm:num_requests_running",
            legend="running {{instance}}",
            extra=[("vllm:num_requests_waiting", "waiting {{instance}}")],
            x=0,
            y=3,
            w=12,
            h=8,
        ),
        _panel(
            3,
            f"Slot saturation (running / {MAX_NUM_SEQS}) per worker",
            f"vllm:num_requests_running / {MAX_NUM_SEQS}",
            legend="{{instance}}",
            x=12,
            y=3,
            w=12,
            h=8,
            unit="percentunit",
        ),
        _panel(
            4,
            "e2e request latency p95 per worker",
            "histogram_quantile(0.95, sum by (le, instance) (rate(vllm:e2e_request_latency_seconds_bucket[5m])))",
            legend="{{instance}}",
            x=0,
            y=11,
            w=12,
            h=8,
            unit="s",
        ),
        _panel(
            5,
            "Request success rate by finished reason per worker",
            "sum by (instance, finished_reason) (rate(vllm:request_success_total[5m]))",
            legend="{{instance}} {{finished_reason}}",
            x=12,
            y=11,
            w=12,
            h=8,
        ),
    ]
    return _dash(
        "inference-scheduler-concurrency",
        "Inference Lab / Scheduler & Concurrency",
        panels,
        ["inference-lab", "vllm", "scheduler"],
    )


def gpu_slices() -> dict:
    note = _text_panel(
        1,
        "How to read this",
        (
            "Both workers share one A100 sliced 50/50 by HAMi "
            "(`nvidia.com/gpucores`/`gpumem-percentage`: 50 each). DCGM reports "
            "device-wide utilization and framebuffer, not per-pod, so these panels "
            "show the shared GPU, not a per-worker split. Compare against the "
            "per-worker KV/scheduler dashboards to see which worker is driving GPU load."
        ),
        x=0,
        y=0,
    )
    panels = [
        note,
        _panel(
            2,
            "vLLM Workers Up (1=Healthy)",
            'up{job="inference-workers"}',
            legend="{{instance}}",
            x=0,
            y=3,
            w=8,
            h=8,
            kind="stat",
            thresholds=UP_THRESHOLDS,
        ),
        _panel(
            3,
            "DCGM GPU utilization %",
            "DCGM_FI_DEV_GPU_UTIL",
            legend="{{instance}}",
            x=8,
            y=3,
            w=8,
            h=8,
            unit="percent",
        ),
        _panel(
            4,
            "DCGM power usage",
            "DCGM_FI_DEV_POWER_USAGE",
            legend="{{instance}}",
            x=16,
            y=3,
            w=8,
            h=8,
            unit="watt",
        ),
        _panel(
            5,
            "DCGM framebuffer used/free",
            "DCGM_FI_DEV_FB_USED * 1024 * 1024",
            legend="used {{instance}}",
            extra=[("DCGM_FI_DEV_FB_FREE * 1024 * 1024", "free {{instance}}")],
            x=0,
            y=11,
            w=12,
            h=8,
            unit="decbytes",
        ),
        _panel(
            6,
            "DCGM memory-copy utilization %",
            "DCGM_FI_DEV_MEM_COPY_UTIL",
            legend="{{instance}}",
            x=12,
            y=11,
            w=12,
            h=8,
            unit="percent",
        ),
        _panel(
            7,
            "Pod restarts (inference-lab)",
            f"kube_pod_container_status_restarts_total{{{WORKER_POD_MATCH}}}",
            legend="{{pod}}",
            x=0,
            y=19,
            w=12,
            h=8,
        ),
        _panel(
            8,
            "Ready replicas (inference-lab)",
            'kube_deployment_status_replicas{deployment=~"inference-worker-a|inference-worker-b"}',
            legend="{{deployment}}",
            x=12,
            y=19,
            w=12,
            h=8,
        ),
        _panel(
            9,
            "Pod phase (inference-lab)",
            f"kube_pod_status_phase{{{WORKER_POD_MATCH}}}",
            legend="{{pod}} {{phase}}",
            x=0,
            y=27,
            w=24,
            h=8,
        ),
    ]
    return _dash(
        "inference-gpu-slices",
        "Inference Lab / GPU & HAMi Slices",
        panels,
        ["inference-lab", "dcgm", "gpu"],
    )


def cluster() -> dict:
    pod = 'pod=~"inference-worker-a.*|inference-worker-b.*|prometheus.*|grafana.*"'
    panels = [
        _panel(
            1,
            "Node Ready",
            'kube_node_status_condition{condition="Ready",status="true"}',
            legend="{{node}}",
            x=0,
            y=0,
            w=6,
            h=6,
            kind="stat",
        ),
        _panel(
            2,
            "vLLM Workers Up (1=Healthy)",
            'up{job="inference-workers"}',
            legend="{{instance}}",
            x=6,
            y=0,
            w=6,
            h=6,
            kind="stat",
            thresholds=UP_THRESHOLDS,
        ),
        _panel(
            3,
            "CPU (node-exporter)",
            '1 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m]))',
            x=12,
            y=0,
            w=6,
            h=6,
            kind="stat",
            unit="percentunit",
        ),
        _panel(
            4,
            "Mem available",
            "node_memory_MemAvailable_bytes",
            x=18,
            y=0,
            w=6,
            h=6,
            kind="stat",
            unit="decbytes",
        ),
        _panel(
            5,
            "Pods by phase",
            "sum by (phase) (kube_pod_status_phase)",
            legend="{{phase}}",
            x=0,
            y=6,
            w=12,
            h=8,
        ),
        _panel(
            6,
            "Restarts",
            f"kube_pod_container_status_restarts_total{{{pod}}}",
            legend="{{pod}}",
            x=12,
            y=6,
            w=12,
            h=8,
        ),
        _panel(
            7,
            "Container CPU",
            f'sum by (pod) (rate(container_cpu_usage_seconds_total{{container!="",{pod}}}[5m]))',
            legend="{{pod}}",
            x=0,
            y=14,
            w=12,
            h=8,
        ),
        _panel(
            8,
            "Container memory",
            f'container_memory_working_set_bytes{{container!="",{pod}}}',
            legend="{{pod}}",
            x=12,
            y=14,
            w=12,
            h=8,
            unit="decbytes",
        ),
        _panel(
            9,
            "DCGM GPU util %",
            "DCGM_FI_DEV_GPU_UTIL",
            legend="{{instance}}",
            x=0,
            y=22,
            w=8,
            h=8,
            unit="percent",
        ),
        _panel(
            10,
            "DCGM framebuffer used",
            "DCGM_FI_DEV_FB_USED * 1024 * 1024",
            legend="{{instance}} FB used",
            x=8,
            y=22,
            w=8,
            h=8,
            unit="decbytes",
        ),
        _panel(
            11,
            "DCGM power",
            "DCGM_FI_DEV_POWER_USAGE",
            legend="{{instance}} power",
            x=16,
            y=22,
            w=8,
            h=8,
            unit="watt",
        ),
    ]
    return _dash(
        "inference-cluster-dcgm",
        "Inference Lab / Cluster & DCGM",
        panels,
        ["class9b", "cluster", "dcgm"],
    )


def _grid(uid: str, title: str, tags: list[str], note: str, specs: list[tuple]) -> dict:
    """Build a dashboard: a note row then panels auto-flowed two per row.

    Each spec is ``(title, expr, legend, opts)``; ``opts`` are `_panel` kwargs (``w``,
    ``h``, ``unit``, ``kind``, ``extra``, ``thresholds``) and a ``w`` of 24 forces a row.
    """
    panels = [_text_panel(1, "How to read this", note, x=0, y=0)]
    x, y = 0, 3
    for pid, (ptitle, expr, legend, opts) in enumerate(specs, start=2):
        w = opts.get("w", 12)
        if x + w > 24:
            x, y = 0, y + 8
        panels.append(
            _panel(pid, ptitle, expr, legend=legend, x=x, y=y, **{"h": 8, **opts})
        )
        x += w
    return _dash(uid, title, panels, ["inference-lab", *tags])


def _q(quantile: str, metric: str, by: str, sel: str = "") -> str:
    keys = f"le, {by}" if by else "le"
    return f"histogram_quantile({quantile}, sum by ({keys}) (rate({metric}_bucket{sel}[5m])))"


def _rate_by(metric: str, by: str) -> str:
    return f"sum by ({by}) (rate({metric}[5m]))"


REQ_OK = 'sum(rate(gateway_requests_total{status="200"}[5m]))'
REQ_ALL = "sum(rate(gateway_requests_total[5m]))"


def overview() -> dict:
    return _grid(
        "inference-overview",
        "Inference Lab / Overview",
        ["gateway", "overview"],
        (
            "Gateway-side view of every request. **Goodput proxy** = share of requests "
            "that ended `200`; it is NOT SLO-aware (a slow 200 still counts). True goodput "
            "(TTFT/E2E within SLO) comes from the replayer artifacts (#123 slice A)."
        ),
        [
            (
                "Requests/s by status",
                _rate_by("gateway_requests_total", "status"),
                "{{status}}",
                {},
            ),
            (
                "Success ratio (status 200)",
                f"{REQ_OK} / clamp_min({REQ_ALL}, 1e-9)",
                "success",
                {"kind": "stat", "unit": "percentunit", "w": 6},
            ),
            (
                "Goodput proxy: 200 req/s (not SLO-aware)",
                REQ_OK,
                "200 req/s",
                {"kind": "stat", "w": 6},
            ),
            (
                "Error taxonomy: non-200 req/s by status and class",
                _rate_by('gateway_requests_total{status!="200"}', "status, class"),
                "{{status}} {{class}}",
                {"kind": "stat"},
            ),
            (
                "Gateway TTFT p50/p95/p99 by class (SLO 0.1s)",
                _q("0.50", "gateway_ttft_seconds", "class"),
                "p50 {{class}}",
                {
                    "unit": "s",
                    "extra": [
                        (_q("0.95", "gateway_ttft_seconds", "class"), "p95 {{class}}"),
                        (_q("0.99", "gateway_ttft_seconds", "class"), "p99 {{class}}"),
                    ],
                },
            ),
            (
                "Gateway stage duration p95",
                _q("0.95", "gateway_request_duration_seconds", "stage"),
                "{{stage}}",
                {"unit": "s"},
            ),
            (
                "Sheds/s by reason",
                _rate_by("orch_shed_total", "reason"),
                "{{reason}}",
                {},
            ),
            (
                "Overflow/s by outcome",
                _rate_by("orch_overflow_total", "outcome"),
                "{{outcome}}",
                {},
            ),
        ],
    )


def gateway_admission() -> dict:
    return _grid(
        "inference-gateway-admission",
        "Inference Lab / Gateway & Admission",
        ["gateway", "admission"],
        (
            "Admission decisions and sheds (`orch_shed_total` carries the HTTP `code`), guard "
            "rejections, per-tenant outcomes, and queue-timeout rejections. `timeout_queue` "
            "means a request waited past its queue budget or remaining deadline."
        ),
        [
            (
                "Admit decisions/s",
                _rate_by("orch_admit_total", "decision, reason, class"),
                "{{decision}} {{reason}} {{class}}",
                {},
            ),
            (
                "Sheds/s by reason and code",
                _rate_by("orch_shed_total", "reason, code"),
                "{{reason}} {{code}}",
                {},
            ),
            (
                "Guard rejects/s",
                _rate_by("guard_reject_total", "reason"),
                "{{reason}}",
                {},
            ),
            (
                "Tenant outcomes/s",
                _rate_by("orch_tenant_total", "tenant, outcome"),
                "{{tenant}} {{outcome}}",
                {},
            ),
            (
                "timeout_queue rejects/s by class",
                _rate_by('queue_error_total{reason="timeout_queue"}', "class"),
                "{{class}}",
                {"w": 24},
            ),
        ],
    )


def router_placement() -> dict:
    return _grid(
        "inference-router-placement",
        "Inference Lab / Router & Placement",
        ["gateway", "router"],
        (
            "Placement picks by policy/worker/reason, placement errors, worker health "
            "(1 = worker currently in that state), snapshot age, and placements forced onto "
            "stale snapshots."
        ),
        [
            (
                "Picks/s by policy, worker, reason",
                _rate_by("orch_pick_total", "policy, worker, reason"),
                "{{policy}} {{worker}} {{reason}}",
                {},
            ),
            (
                "Placement errors/s",
                _rate_by("placement_error_total", "reason"),
                "{{reason}}",
                {},
            ),
            (
                "Worker health (1 = in state)",
                "worker_health",
                "{{worker}} {{state}}",
                {},
            ),
            ("Worker warm (0 = healthy but cold)", "worker_warm", "{{worker}}", {}),
            (
                "Snapshot age",
                "worker_snapshot_age_seconds",
                "{{worker}}",
                {"unit": "s"},
            ),
            (
                "Stale-snapshot fallbacks/s",
                "sum(rate(stale_snapshot_fallback_total[5m]))",
                "fallbacks",
                {"w": 24},
            ),
        ],
    )


def queues() -> dict:
    wait = "orch_queue_wait_seconds"

    def p99(klass: str) -> str:
        return _q("0.99", wait, "", f'{{class="{klass}"}}')

    return _grid(
        "inference-queues",
        "Inference Lab / Queues",
        ["gateway", "queues"],
        (
            "Per-worker gateway queues. The spread panel is p99 wait of `batch` minus "
            "`interactive`: it grows when batch work queues while interactive stays fast "
            "(class isolation working) and collapses toward 0 when both wait alike."
        ),
        [
            (
                "Queue depth by worker and class",
                "orch_replica_queue_depth",
                "{{worker}} {{class}}",
                {},
            ),
            (
                "Queue wait p50/p95/p99 by class",
                _q("0.50", wait, "class"),
                "p50 {{class}}",
                {
                    "unit": "s",
                    "extra": [
                        (_q("0.95", wait, "class"), "p95 {{class}}"),
                        (_q("0.99", wait, "class"), "p99 {{class}}"),
                    ],
                },
            ),
            (
                "p99 wait spread: batch minus interactive",
                f'{p99("batch")} - {p99("interactive")}',
                "batch - interactive",
                {"unit": "s"},
            ),
            (
                "Queue rejects/s by reason and class",
                _rate_by("queue_error_total", "reason, class"),
                "{{reason}} {{class}}",
                {},
            ),
        ],
    )


def overflow() -> dict:
    return _grid(
        "inference-overflow",
        "Inference Lab / Overflow",
        ["gateway", "overflow"],
        (
            "Overflow to the external provider (off by default). `reason` is the original "
            "local shed reason; `outcome` is what the overflow attempt did."
        ),
        [
            (
                "Overflow attempts/s",
                _rate_by("orch_overflow_total", "reason, provider, model, outcome"),
                "{{reason}} {{provider}} {{model}} {{outcome}}",
                {},
            ),
            (
                "Overflow errors/s",
                _rate_by("overflow_error_total", "reason"),
                "{{reason}}",
                {},
            ),
        ],
    )


def memory_proof() -> dict:
    return _grid(
        "inference-memory-proof",
        "Inference Lab / Memory Proof",
        ["vllm", "dcgm", "memory"],
        (
            "Shared time axis (crosshair is linked): GPU framebuffer (DCGM, device-wide) "
            "next to vLLM KV usage, running/waiting requests, request rate, preemptions, and "
            "prefix-cache hit ratio. Use it to show KV pressure driving queueing and "
            "preemption, not just slot limits."
        ),
        [
            (
                "DCGM framebuffer used/free",
                "DCGM_FI_DEV_FB_USED * 1024 * 1024",
                "used {{instance}}",
                {
                    "unit": "decbytes",
                    "extra": [
                        ("DCGM_FI_DEV_FB_FREE * 1024 * 1024", "free {{instance}}")
                    ],
                },
            ),
            (
                "KV cache usage %",
                "vllm:kv_cache_usage_perc",
                "{{instance}}",
                {"unit": "percentunit"},
            ),
            (
                "Running vs waiting requests",
                "vllm:num_requests_running",
                "running {{instance}}",
                {"extra": [("vllm:num_requests_waiting", "waiting {{instance}}")]},
            ),
            (
                "Request rate (completed/s)",
                _rate_by("vllm:request_success_total", "instance"),
                "{{instance}}",
                {},
            ),
            (
                "Preemptions/s",
                _rate_by("vllm:num_preemptions_total", "instance"),
                "{{instance}}",
                {},
            ),
            (
                "Prefix cache hit ratio",
                (
                    "sum by (instance) (rate(vllm:prefix_cache_hits_total[5m])) / "
                    "clamp_min(sum by (instance) (rate(vllm:prefix_cache_queries_total[5m])), 1e-9)"
                ),
                "{{instance}}",
                {"unit": "percentunit"},
            ),
        ],
    )


def kv_hop_stub() -> dict:
    body = (
        "Placeholder. Real KV-transfer/hop metrics (Mooncake/LMCache-style) arrive with "
        "**#133**; no hop metrics are exported today, so no panels are drawn here."
    )
    return _dash(
        "inference-kv-hop",
        "Inference Lab / KV Hop (stub)",
        [_text_panel(1, "KV hop metrics: not yet available", body, x=0, y=0)],
        ["inference-lab", "kv-hop", "stub"],
    )


DASHBOARDS = {
    "kv_prefix_cache": kv_prefix_cache,
    "prefill_decode": prefill_decode,
    "scheduler_concurrency": scheduler_concurrency,
    "gpu_slices": gpu_slices,
    "cluster": cluster,
    "overview": overview,
    "gateway_admission": gateway_admission,
    "router_placement": router_placement,
    "queues": queues,
    "overflow": overflow,
    "memory_proof": memory_proof,
    "kv_hop_stub": kv_hop_stub,
}


def write_dashboards(dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, builder in DASHBOARDS.items():
        path = dest / f"{name}.json"
        path.write_text(json.dumps(builder(), indent=2, sort_keys=True) + "\n")
        written.append(path)
    return written


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    write_dashboards(here / "dashboards")
