"""Load one run directory (written by services/app/scripts/run_scenario.py) and summarize it.

Evidence scopes (every result dict carries one under "scope"):
  PER_REQUEST  fields measured per request (requests.jsonl).
  WINDOW       Prometheus isolated-window aggregates / range series; never per request.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PER_REQUEST = "per-request (requests.jsonl)"
WINDOW = "WINDOW-level aggregate (Prometheus); NOT attributable to any single request"

_TOL = 0.02  # relative goodput gain needed to count as "still improving"


@dataclass
class Run:
    path: Path
    requests: list[dict[str, Any]]
    summary: dict[str, Any]
    sweep: list[dict[str, Any]]
    manifest: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return self.manifest.get("policy_under_test") or self.path.name

    @property
    def levels(self) -> list[dict[str, Any]]:
        return self.summary.get("levels", [])

    def level_record(self, level: int | None = None) -> dict[str, Any]:
        """summary.json level record; explicit level required when the run swept several."""
        if level is None:
            if len(self.levels) != 1:
                offered = [lv["offered_concurrency"] for lv in self.levels]
                raise ValueError(f"{self.path.name} swept {offered}; pass level=")
            return self.levels[0]
        for lv in self.levels:
            if lv["offered_concurrency"] == level:
                return lv
        raise ValueError(f"{self.path.name} has no level {level}")

    def select(self, level: int | None = None) -> list[dict[str, Any]]:
        if level is None and len(self.levels) <= 1:
            return self.requests
        want = self.level_record(level)["offered_concurrency"]
        return [r for r in self.requests if r.get("offered_concurrency") == want]


def _read_json(path: Path, default: Any, warnings: list[str]) -> Any:
    if not path.is_file():
        warnings.append(f"missing {path.name}")
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _num(v: str) -> Any:
    if v in ("n/a", ""):
        return None
    try:
        return int(v)
    except ValueError:
        return float(v)


def load_run(path: str | Path) -> Run:
    """Load requests.jsonl, summary.json, sweep.json|csv and manifest.json (missing => warning)."""
    p = Path(path)
    if not p.is_dir():
        raise FileNotFoundError(f"run directory not found: {p}")
    warns: list[str] = []
    reqs: list[dict[str, Any]] = []
    rq = p / "requests.jsonl"
    if rq.is_file():
        reqs = [json.loads(x) for x in rq.read_text(encoding="utf-8").splitlines() if x]
    else:
        warns.append("missing requests.jsonl")
    if (p / "sweep.json").is_file():
        sweep = json.loads((p / "sweep.json").read_text(encoding="utf-8"))
    elif (p / "sweep.csv").is_file():
        with open(p / "sweep.csv", encoding="utf-8", newline="") as f:
            sweep = [{k: _num(v) for k, v in row.items()} for row in csv.DictReader(f)]
    else:
        sweep = []
        warns.append("missing sweep.json/sweep.csv")
    return Run(
        p,
        reqs,
        _read_json(p / "summary.json", {}, warns),
        sweep,
        _read_json(p / "manifest.json", {}, warns),
        warns,
    )


def percentiles(values: list[float]) -> dict[str, float | None]:
    """p50/p95/p99, linear interpolation (same method as the replayer); None when empty."""
    if not values:
        return {"p50": None, "p95": None, "p99": None}
    s = sorted(values)

    def p(q: float) -> float:
        k = (len(s) - 1) * q
        f = int(k)
        return s[f] + (k - f) * (s[min(f + 1, len(s) - 1)] - s[f])

    return {"p50": round(p(0.5), 2), "p95": round(p(0.95), 2), "p99": round(p(0.99), 2)}


def header(r: dict[str, Any], name: str) -> str | None:
    return (r.get("gateway_headers") or {}).get(name)


def queue_wait_ms(r: dict[str, Any]) -> float | None:
    try:
        return float(header(r, "x-queue-wait-ms") or "")
    except ValueError:
        return None


def completed(rs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rs if r.get("status") == "completed"]


def latency_stats(rs: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts plus TTFT/E2E/queue-wait percentiles (TTFT and E2E over completed requests)."""
    ok = completed(rs)
    return {
        "requests": len(rs),
        "completed": len(ok),
        "good": sum(bool(r.get("good")) for r in rs),
        "ttft_ms": percentiles(
            [r["server_ttft_ms"] for r in ok if r.get("server_ttft_ms") is not None]
        ),
        "e2e_ms": percentiles([r["client_duration_ms"] for r in ok]),
        "queue_wait_ms": percentiles(
            [w for r in rs if (w := queue_wait_ms(r)) is not None]
        ),
    }


_KEYS = {
    "workload_class": lambda r: r.get("workload_class"),
    "tenant": lambda r: r.get("tenant_id"),
    "worker": lambda r: header(r, "x-place-decision"),
}


def breakdown(run: Run, by: str, level: int | None = None) -> dict[str, Any]:
    """Latency percentiles by workload_class | tenant | worker (worker = x-place-decision)."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in run.select(level):
        groups.setdefault(_KEYS[by](r) or "(none)", []).append(r)
    return {
        "scope": PER_REQUEST,
        "by": by,
        "rows": {k: latency_stats(v) for k, v in sorted(groups.items())},
    }


def ttft_by_turn(run: Run, level: int | None = None) -> dict[str, Any]:
    groups: dict[int, list[dict[str, Any]]] = {}
    for r in run.select(level):
        groups.setdefault(r["turn_index"] + 1, []).append(r)
    return {
        "scope": PER_REQUEST,
        "rows": {f"turn_{k}": latency_stats(v) for k, v in sorted(groups.items())},
    }


def sweep_curve(run: Run) -> dict[str, Any]:
    """Offered load vs throughput vs goodput plus the knee.

    Knee = the largest offered load whose goodput beats the best goodput at every lower load by
    more than 2 percent (goodput still improving). Goodput is good tokens/s when every level
    measured tokens, else good requests/s; "n/a" token rates never enter the comparison.
    """
    rows = sorted(run.sweep, key=lambda r: r["offered_concurrency"])
    out = []
    for r in rows:
        n, ok = r.get("requests") or 0, r.get("successful") or 0
        out.append(
            {
                "offered_concurrency": r["offered_concurrency"],
                "attempted_rps": r.get("requests_per_s"),
                "completed_rps": round(r["requests_per_s"] * ok / n, 2) if n else None,
                "tokens_per_s": r.get("tokens_per_s"),
                "good_rps": r.get("good_requests_per_s"),
                "good_tokens_per_s": r.get("good_tokens_per_s"),
                "ttft_p95_ms": r.get("ttft_p95_ms"),
                "e2e_p95_ms": r.get("e2e_p95_ms"),
            }
        )
    warnings: list[str] = []
    use_tokens = bool(out) and all(o["good_tokens_per_s"] is not None for o in out)
    metric = "good_tokens_per_s" if use_tokens else "good_rps"
    if out and not use_tokens:
        warnings.append(
            "token rates unavailable (n/a) at some levels; knee uses good_rps"
        )
    knee = None
    best = None
    for o in out:
        g = o[metric]
        if g is None:
            continue
        if best is None or g > best * (1 + _TOL):
            knee = o["offered_concurrency"]
        best = g if best is None else max(best, g)
    if knee is not None and len(out) > 1 and knee == out[-1]["offered_concurrency"]:
        warnings.append(
            "goodput still improving at the highest offered load; knee not reached"
        )
    return {
        "scope": PER_REQUEST + "; rates are run-level (requests / duration)",
        "rows": out,
        "knee_metric": metric,
        "knee_offered_concurrency": knee,
        "warnings": warnings,
    }
