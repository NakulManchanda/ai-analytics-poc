from __future__ import annotations

import re
import time

import httpx
from pydantic import BaseModel, Field

# Pattern matching a Prometheus metric line:
# name{k="v",...} 123.45 [optional timestamp]
# or
# name 123.45 [optional timestamp]
_PROMETHEUS_LINE_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)"
    r"(?:\{(?P<labels>[^}]*)\})?"
    r"\s+(?P<value>[+-]?(?:[0-9]*\.[0-9]+|[0-9]+)(?:[eE][+-]?[0-9]+)?|NaN|[+-]?Inf)"
    r"(?:\s+(?P<timestamp>\d+))?$"
)

_LABEL_RE = re.compile(r'(?P<key>[a-zA-Z_][a-zA-Z0-9_]*)="(?P<val>(?:\\.|[^"\\])*)"')
_ESCAPE_RE = re.compile(r'\\(["\\ntr])')
_ESCAPE_MAP = {'"': '"', "\\": "\\", "n": "\n", "t": "\t", "r": "\r"}


def parse_labels(labels_str: str | None) -> dict[str, str]:
    if not labels_str or not labels_str.strip():
        return {}
    res = {}
    for match in _LABEL_RE.finditer(labels_str):
        val = _ESCAPE_RE.sub(
            lambda m: _ESCAPE_MAP.get(m.group(1), m.group(0)), match.group("val")
        )
        res[match.group("key")] = val
    return res


class MetricSample(BaseModel):
    name: str
    labels: dict[str, str] = Field(default_factory=dict)
    value: float


class PrometheusMetricSnapshot(BaseModel):
    timestamp: float = Field(default_factory=time.time)
    samples: list[MetricSample] = Field(default_factory=list)

    @classmethod
    def from_text(
        cls, text: str, timestamp: float | None = None
    ) -> PrometheusMetricSnapshot:
        ts = timestamp if timestamp is not None else time.time()
        samples: list[MetricSample] = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = _PROMETHEUS_LINE_RE.match(line)
            if not m:
                continue
            name = m.group("name")
            val_str = m.group("value")
            try:
                val = float(val_str)
            except ValueError:
                continue
            labels = parse_labels(m.group("labels"))
            samples.append(MetricSample(name=name, labels=labels, value=val))
        return cls(timestamp=ts, samples=samples)

    @classmethod
    async def scrape(
        cls, url: str, client: httpx.AsyncClient | None = None, timeout: float = 10.0
    ) -> PrometheusMetricSnapshot:
        if client is not None:
            resp = await client.get(url, timeout=timeout)
            resp.raise_for_status()
            return cls.from_text(resp.text)
        async with httpx.AsyncClient() as c:
            resp = await c.get(url, timeout=timeout)
            resp.raise_for_status()
            return cls.from_text(resp.text)

    @classmethod
    def scrape_sync(cls, url: str, timeout: float = 10.0) -> PrometheusMetricSnapshot:
        with httpx.Client() as c:
            resp = c.get(url, timeout=timeout)
            resp.raise_for_status()
            return cls.from_text(resp.text)

    def get_sum(self, name: str) -> float:
        """Sum all samples matching name or its alternate name (colon vs underscore)."""
        alt_name = name.replace(":", "_") if ":" in name else name.replace("_", ":", 1)
        total = 0.0
        found = False
        for s in self.samples:
            if s.name == name or s.name == alt_name:
                total += s.value
                found = True
        return total if found else 0.0

    def has_metric(self, name: str) -> bool:
        alt_name = name.replace(":", "_") if ":" in name else name.replace("_", ":", 1)
        return any(s.name == name or s.name == alt_name for s in self.samples)

    def get_first_value(self, name: str) -> float | None:
        alt_name = name.replace(":", "_") if ":" in name else name.replace("_", ":", 1)
        for s in self.samples:
            if s.name == name or s.name == alt_name:
                return s.value
        return None


class MetricsDelta(BaseModel):
    duration_seconds: float
    counter_reset_detected: bool = False
    prefix_cache_hits: float = 0.0
    prefix_cache_queries: float = 0.0
    prefix_cache_hit_rate_pct: float = 0.0
    prompt_tokens: float = 0.0
    generation_tokens: float = 0.0
    avg_ttft_seconds: float | None = None
    avg_queue_time_seconds: float | None = None
    gpu_cache_usage_post: float | None = None
    avg_prompt_throughput: float | None = None
    avg_generation_throughput: float | None = None
    raw_deltas: dict[str, float] = Field(default_factory=dict)


def compute_metrics_delta(
    before: PrometheusMetricSnapshot, after: PrometheusMetricSnapshot
) -> MetricsDelta:
    duration = max(after.timestamp - before.timestamp, 0.0)
    counter_reset = False

    # Target counters to calculate delta for
    counters = [
        "vllm:prefix_cache_hits_total",
        "vllm:prefix_cache_queries_total",
        "vllm:prompt_tokens_total",
        "vllm:generation_tokens_total",
        "vllm:time_to_first_token_seconds_count",
        "vllm:time_to_first_token_seconds_sum",
        "vllm:request_queue_time_seconds_count",
        "vllm:request_queue_time_seconds_sum",
    ]

    deltas: dict[str, float] = {}
    for c in counters:
        b_val = before.get_sum(c)
        a_val = after.get_sum(c)
        if a_val < b_val:
            counter_reset = True
            delta = a_val  # Assume reset to 0
        else:
            delta = a_val - b_val
        deltas[c] = delta

    hits = deltas.get("vllm:prefix_cache_hits_total", 0.0)
    queries = deltas.get("vllm:prefix_cache_queries_total", 0.0)
    hit_rate = (hits / queries * 100.0) if queries > 0 else 0.0

    # TTFT avg calculation
    ttft_count = deltas.get("vllm:time_to_first_token_seconds_count", 0.0)
    ttft_sum = deltas.get("vllm:time_to_first_token_seconds_sum", 0.0)
    avg_ttft = (ttft_sum / ttft_count) if ttft_count > 0 else None

    # Queue time avg calculation
    queue_count = deltas.get("vllm:request_queue_time_seconds_count", 0.0)
    queue_sum = deltas.get("vllm:request_queue_time_seconds_sum", 0.0)
    avg_queue = (queue_sum / queue_count) if queue_count > 0 else None

    # Gauges snapshot from after (prefer kv_cache_usage_perc, fallback to gpu_cache_usage_factor)
    gpu_cache_usage = after.get_first_value("vllm:kv_cache_usage_perc")
    if gpu_cache_usage is None:
        gpu_cache_usage = after.get_first_value("vllm:gpu_cache_usage_factor")

    # Throughput: read gauge if emitted by vllm, otherwise derive from token deltas over duration
    avg_prompt_tp = after.get_first_value("vllm:avg_prompt_throughput_tok_per_s")
    if avg_prompt_tp is None and duration > 0:
        p_tokens = deltas.get("vllm:prompt_tokens_total", 0.0)
        avg_prompt_tp = round(p_tokens / duration, 2)

    avg_gen_tp = after.get_first_value("vllm:avg_generation_throughput_tok_per_s")
    if avg_gen_tp is None and duration > 0:
        g_tokens = deltas.get("vllm:generation_tokens_total", 0.0)
        avg_gen_tp = round(g_tokens / duration, 2)

    return MetricsDelta(
        duration_seconds=duration,
        counter_reset_detected=counter_reset,
        prefix_cache_hits=hits,
        prefix_cache_queries=queries,
        prefix_cache_hit_rate_pct=hit_rate,
        prompt_tokens=deltas.get("vllm:prompt_tokens_total", 0.0),
        generation_tokens=deltas.get("vllm:generation_tokens_total", 0.0),
        avg_ttft_seconds=avg_ttft,
        avg_queue_time_seconds=avg_queue,
        gpu_cache_usage_post=gpu_cache_usage,
        avg_prompt_throughput=avg_prompt_tp,
        avg_generation_throughput=avg_gen_tp,
        raw_deltas=deltas,
    )
