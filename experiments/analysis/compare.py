"""Paired-run comparisons for E2 (prefix reuse), E3 (routing) and E4 (admission)."""

from __future__ import annotations

from collections import Counter
from typing import Any

from experiments.analysis.runs import (
    PER_REQUEST,
    WINDOW,
    Run,
    completed,
    header,
    latency_stats,
    percentiles,
    ttft_by_turn,
)

# Mirrors app.benchmarks.parity.EQUAL_FIELDS plus the run-level inputs (kept stdlib-only here).
MUST_MATCH = (
    "scenario.sha256",
    "system_prefix.prefix_chars",
    "system_prefix.exact_tokens",
    "slos",
    "topology",
    "model_revision",
    "tokenizer_revision",
    "chat_template_revision",
    "engine_flags",
    "vllm_version",
    "kv_block_size",
    "max_tokens",
    "offered_concurrency_levels",
)
# Execution controls: identical unless listed in `varied` (the one treatment of the experiment).
EXECUTION_FIELDS = (
    "policy_override",
    "admission_mode",
    "router_label",
    "gateway_stream",
    "strategy",
    "target_endpoint_type",
)
CONTROLS = ("policy_override", "admission_mode")
_UNKNOWN = {"", "unknown", "none", "null", "tbd", "?", "n/a"}


def _dig(m: dict[str, Any], dotted: str) -> Any:
    for part in dotted.split("."):
        m = (m or {}).get(part) if isinstance(m, dict) else None
    return m


def _unprovable(v: Any) -> bool:
    return (
        v is None
        or (not isinstance(v, (list, dict)) and str(v).strip().lower() in _UNKNOWN)
        or v in ([], {})
    )


def _execution(m: dict[str, Any], f: str) -> Any:
    v = _dig(m, f"execution.{f}")
    if v is None and f in CONTROLS:  # manifests from before PR #151 lack "execution"
        v = m.get(f"{f}_requested")
    return v


def _unverified(m: dict[str, Any]) -> list[tuple[str, str]]:
    """(control, detail) for every REQUESTED control not strictly verified in this manifest.

    A requested control needs `<c>_verified is True`, no mismatched echoes in control_observations
    and no control_not_applied turns. Controls requested in neither run need nothing.
    """
    out = []
    requested = [c for c in (*CONTROLS, "force_worker") if m.get(f"{c}_requested")]
    for c in requested:
        bad = ((m.get("control_observations") or {}).get(c) or {}).get(
            "mismatched"
        ) or 0
        if m.get(f"{c}_verified") is not True:
            out.append((c, f"{c}_verified={m.get(f'{c}_verified')!r}"))
        elif bad:
            out.append((c, f"{bad} mismatched echoes"))
    if requested and m.get("control_unverified_turns"):
        out.append(
            (requested[0], f"{m['control_unverified_turns']} control_unverified turns")
        )
    return out


def check_manifests(a: Run, b: Run, varied: tuple[str, ...] = ()) -> dict[str, Any]:
    """Prove control and treatment ran the same inputs.

    match      no concrete value differs (inputs and non-varied execution controls).
    proven     every required field is concrete in BOTH manifests (missing/unknown => unprovable).
    comparable match, proven (incl. every requested non-varied control verified), and every `varied`
               control concrete, unequal AND verified (no unverified turns) in BOTH runs
               (treatment_varied / treatment_problems); E2 passes no `varied`.
    Execution controls (policy, admission, router, stream, ...) must be identical except `varied`.
    """
    ma, mb = a.manifest, b.manifest
    mismatches, unprovable, warnings, treatment_problems = [], [], [], []
    if not ma or not mb:
        unprovable.append("manifest (missing in at least one run)")
    else:
        for f in MUST_MATCH:
            va, vb = _dig(ma, f), _dig(mb, f)
            if _unprovable(va) or _unprovable(vb):
                unprovable.append(f)
            elif va != vb:
                mismatches.append({"field": f, "a": va, "b": vb})
        for f in EXECUTION_FIELDS:
            va, vb = _execution(ma, f), _execution(mb, f)
            if f in varied:
                if _unprovable(va) or _unprovable(vb):
                    treatment_problems.append(
                        {
                            "field": f"execution.{f}",
                            "reason": "unprovable",
                            "a": va,
                            "b": vb,
                        }
                    )
                elif va == vb:
                    treatment_problems.append(
                        {
                            "field": f"execution.{f}",
                            "reason": "same value in both runs",
                            "a": va,
                            "b": vb,
                        }
                    )
                continue
            # policy/admission may legitimately be None (not requested) in both runs.
            if f not in CONTROLS and (va is None or vb is None):
                unprovable.append(f"execution.{f}")
            elif va != vb:
                mismatches.append({"field": f"execution.{f}", "a": va, "b": vb})
        for name, m in (("a", ma), ("b", mb)):
            for c, detail in _unverified(m):
                item = {
                    "field": c,
                    "run": name,
                    "reason": "unverified",
                    "detail": detail,
                }
                if c in varied:
                    treatment_problems.append(item)
                else:
                    unprovable.append(f"{c} verification (run {name}: {detail})")
                warnings.append(
                    f"run {name}: {c} requested but NOT verified ({detail})"
                )
    if unprovable:
        warnings.append(
            "NOT PROVEN like-for-like; missing/unknown: " + ", ".join(unprovable)
        )
    for t in treatment_problems:
        warnings.append(f"TREATMENT NOT VARIED/PROVEN: {t['field']} {t['reason']}")
    if varied and (not ma or not mb):
        treatment_problems.append({"field": "manifest", "reason": "unprovable"})
    match, proven = not mismatches, not unprovable
    return {
        "match": match,
        "proven": proven,
        "treatment_varied": bool(varied) and not treatment_problems,
        "treatment_problems": treatment_problems,
        "comparable": match and proven and not treatment_problems,
        "mismatches": mismatches,
        "unprovable_fields": unprovable,
        "warnings": warnings,
    }


def _gate(
    check: dict[str, Any], scope: str, body: dict[str, Any], warns: list[str]
) -> dict[str, Any]:
    """Numbers appear at top level only for a proven like-for-like pair."""
    out: dict[str, Any] = {
        "scope": scope,
        "comparable": check["comparable"],
        "manifest_check": check,
    }
    if check["comparable"]:
        return {**out, **body, "warnings": warns}
    why = (
        "differing inputs"
        if check["mismatches"]
        else (
            "unprovable parity metadata"
            if not check["proven"]
            else "treatment not varied or unprovable"
        )
    )
    return {
        **out,
        "warnings": [
            f"NOT COMPARABLE ({why}): numbers shown only under not_comparable_numbers"
        ]
        + warns,
        "not_comparable_numbers": body,
    }


def _goodput(run: Run, level: int | None) -> dict[str, Any]:
    s = run.level_record(level)["summary"]
    keys = (
        "requests_per_s",
        "tokens_per_s",
        "good_requests_per_s",
        "good_tokens_per_s",
    )
    return {k: s.get(k) for k in keys}


def _deadline_met(run: Run, rs: list[dict[str, Any]]) -> float | None:
    default = (run.manifest.get("slos") or {}).get("default_e2e_slo_ms", 3500.0)
    if not rs:
        return None
    met = sum(
        r["status"] == "completed"
        and r["client_duration_ms"] <= (r.get("deadline_ms") or default)
        for r in rs
    )
    return round(met / len(rs), 3)


def _reasons_by_turn(rs: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    out: dict[str, Counter] = {}
    for r in rs:
        if reason := header(r, "x-placement-reason"):
            out.setdefault(f"turn_{r['turn_index'] + 1}", Counter())[reason] += 1
    return {k: dict(sorted(v.items())) for k, v in sorted(out.items())}


def _delta(
    a: dict[str, Any], b: dict[str, Any], keys: list[tuple[str, ...]]
) -> dict[str, float | None]:
    def get(d: dict[str, Any], path: tuple[str, ...]) -> Any:
        for p in path:
            d = d[p]
        return d

    out = {}
    for path in keys:
        va, vb = get(a, path), get(b, path)
        out[".".join(path)] = None if va is None or vb is None else round(vb - va, 2)
    return out


def e3_compare(ll: Run, ptl: Run, level: int | None = None) -> dict[str, Any]:
    """least_loaded (a) vs prefix_then_load (b) on the same scenario. delta = b - a."""
    runs = {}
    for name, run in (("a_least_loaded", ll), ("b_prefix_then_load", ptl)):
        rs = run.select(level)
        runs[name] = {
            "label": run.label,
            **latency_stats(rs),
            "deadline_met_rate": _deadline_met(run, rs),
            "goodput": _goodput(run, level),
            "worker_distribution": dict(
                Counter(header(r, "x-place-decision") or "(none)" for r in rs)
            ),
            "placement_reason_by_turn": _reasons_by_turn(rs),
        }
    a, b = runs["a_least_loaded"], runs["b_prefix_then_load"]
    check = check_manifests(ll, ptl, varied=("policy_override",))
    keys = [("ttft_ms", p) for p in ("p50", "p95", "p99")] + [("queue_wait_ms", "p95")]
    keys += [("goodput", "good_requests_per_s"), ("deadline_met_rate",)]
    return _gate(
        check,
        PER_REQUEST + "; goodput is run-level",
        {"runs": runs, "delta_b_minus_a": _delta(a, b, keys)},
        list(check["warnings"]),
    )


def jain_index(values: list[float]) -> float | None:
    """(sum x)^2 / (n * sum x^2): 1.0 = perfectly equal, 1/n = one party gets everything."""
    sq = sum(v * v for v in values)
    return round(sum(values) ** 2 / (len(values) * sq), 3) if values and sq else None


def outcome_reason(r: dict[str, Any]) -> str | None:
    """Why a request did not complete (gateway decision headers first); None if completed."""
    if r["status"] == "completed":
        return None
    guard, admit = (
        header(r, "x-guard-decision") or "",
        header(r, "x-admit-decision") or "",
    )
    queue = header(r, "x-queue-decision")
    if guard.startswith("reject"):
        return guard.replace("reject:", "guard:")
    if admit.startswith("shed:"):
        return admit
    if queue and queue != "dispatched":
        return queue if queue == "timeout_queue" else f"queue:{queue}"
    return r["status"]


def _fairness(rs: list[dict[str, Any]]) -> dict[str, Any]:
    tenants: dict[str, list[dict[str, Any]]] = {}
    for r in rs:
        tenants.setdefault(r.get("tenant_id") or "(none)", []).append(r)
    tokens_ok = all(r.get("tokens_out") is not None for r in completed(rs))
    per = {}
    for t, g in sorted(tenants.items()):
        ok = completed(g)
        per[t] = {
            "offered": len(g),
            "admitted": sum(
                not (outcome_reason(r) or "").startswith(
                    ("shed:", "guard:", "http_429")
                )
                for r in g
            ),
            "completed": len(ok),
            "completed_tokens": sum(r["tokens_out"] for r in ok) if tokens_ok else None,
            "served_fraction": round(len(ok) / len(g), 3),
        }
    return {
        "per_tenant": per,
        "jain_completed_requests": jain_index([v["completed"] for v in per.values()]),
        "jain_completed_tokens": (
            jain_index([v["completed_tokens"] for v in per.values()])
            if tokens_ok
            else None
        ),
        "jain_served_fraction": jain_index(
            [v["served_fraction"] for v in per.values()]
        ),
    }


def _starvation(rs: list[dict[str, Any]], ratio: float) -> dict[str, Any]:
    stats = {
        c: latency_stats([r for r in rs if r.get("workload_class") == c])
        for c in ("interactive", "batch")
    }
    frac = {
        c: (s["completed"] / s["requests"] if s["requests"] else None)
        for c, s in stats.items()
    }
    bq, iq = (
        stats["batch"]["queue_wait_ms"]["p99"],
        stats["interactive"]["queue_wait_ms"]["p99"],
    )
    fi, fb = frac["interactive"], frac["batch"]
    starved = fb is not None and fi is not None and fi > 0 and fb < ratio * fi
    return {
        "completed_fraction": frac,
        "class_p99_spread_ms": None if bq is None or iq is None else round(bq - iq, 2),
        "batch_starved": starved,
        "note": "a favorable class_p99_spread is NOT success if batch_starved is true",
    }


def e4_compare(
    off: Run, on: Run, level: int | None = None, starve_ratio: float = 0.5
) -> dict[str, Any]:
    """Admission off (a) vs on (b). delta = b - a."""
    runs = {}
    for name, run in (("a_admission_off", off), ("b_admission_on", on)):
        rs = run.select(level)
        reasons = Counter(x for r in rs if (x := outcome_reason(r)))
        runs[name] = {
            "label": run.label,
            **latency_stats(rs),
            "goodput": _goodput(run, level),
            "not_completed_by_reason": dict(sorted(reasons.items())),
            "sheds_by_reason": {
                k: v for k, v in sorted(reasons.items()) if k.startswith("shed:")
            },
            "timeout_queue": reasons.get("timeout_queue", 0),
            "fairness": _fairness(rs),
            "batch_starvation": _starvation(rs, starve_ratio),
        }
    a, b = runs["a_admission_off"], runs["b_admission_on"]
    check = check_manifests(off, on, varied=("admission_mode",))
    warns = list(check["warnings"])
    for name, r in runs.items():
        if r["batch_starvation"]["batch_starved"]:
            warns.append(
                f"{name}: batch starved (completed fraction < {starve_ratio} x interactive)"
            )
    delta = _delta(
        a,
        b,
        [("goodput", "good_requests_per_s"), ("e2e_ms", "p99"), ("ttft_ms", "p99")],
    )
    return _gate(
        check,
        PER_REQUEST + "; goodput is run-level",
        {"runs": runs, "delta_b_minus_a": delta},
        warns,
    )


def e2_prefix_reuse(
    cold: Run,
    reused: Run,
    cold_level: int | None = None,
    reused_level: int | None = None,
) -> dict[str, Any]:
    """Cold vs reused TTFT (per-request) and prefix-cache hit-rate deltas (WINDOW-level only)."""
    # Only cache state may differ: policy, admission, router, stream, scenario, SLOs, ... identical.
    check = check_manifests(cold, reused)
    warns = list(check["warnings"])
    per_request, window = {}, {}
    for name, run, lv in (("cold", cold, cold_level), ("reused", reused, reused_level)):
        rs = run.select(lv)
        per_request[name] = {
            "label": run.label,
            "ttft_ms": percentiles(
                [
                    r["server_ttft_ms"]
                    for r in completed(rs)
                    if r.get("server_ttft_ms") is not None
                ]
            ),
            "ttft_by_turn": ttft_by_turn(run, lv)["rows"],
        }
        d = (run.level_record(lv).get("prometheus_window") or {}).get("delta")
        if d is None:
            warns.append(
                f"{name}: no prometheus_window delta (run without --metrics-url)"
            )
        elif d.get("counter_reset_detected"):
            warns.append(f"{name}: counter reset detected in window; deltas unreliable")
        window[name] = d and {
            k: d.get(k)
            for k in (
                "prefix_cache_hits",
                "prefix_cache_queries",
                "prefix_cache_hit_rate_pct",
            )
        }
    hr = [
        (window[n] or {}).get("prefix_cache_hit_rate_pct") for n in ("cold", "reused")
    ]
    body = {
        "per_request": {"scope": PER_REQUEST, **per_request},
        "window": {
            "scope": WINDOW,
            **window,
            "hit_rate_pct_delta_reused_minus_cold": (
                None if None in hr else round(hr[1] - hr[0], 2)
            ),
        },
    }
    warns.append(
        "window hit rate is aggregate over the whole run: never attribute it to one request"
    )
    return _gate(check, PER_REQUEST + " and " + WINDOW, body, warns)
