"""Single-request proof: join one requests.jsonl record with the gateway decision log (#123).

Reads a run directory written by ``run_scenario.py`` (``requests.jsonl``, ``summary.json``,
``manifest.json``) plus an optional pulled gateway log (JSON decision lines, e.g. from
``kubectl logs``) and builds a stage timeline for ONE request_id:

    app -> guard -> admission -> placement -> queue -> hop -> engine -> TTFT/decode
        -> tool call -> next agent step

Every stage carries a ``status`` and a ``scope``. ``per-request`` values come from that request's
own record/log lines. ``window`` values are isolated-window Prometheus aggregates and are shown
only as context, never as this request's own numbers. Anything not logged is reported as
``unavailable`` with the reason instead of being estimated.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.benchmarks.goodput import Slos, is_good
from app.benchmarks.replayer import TurnResult

# hop_result values from the #133 transfer contract that mean blocks really moved.
CONFIRMED_HOP_RESULTS = ("transferred",)
WINDOW_KEYS = (
    "prefix_cache_hits",
    "prefix_cache_queries",
    "prefix_cache_hit_rate_pct",
    "prompt_tokens",
    "generation_tokens",
    "avg_ttft_seconds",
    "avg_queue_time_seconds",
    "gpu_cache_usage_post",
    "counter_reset_detected",
)
WINDOW_NOTE = (
    "isolated-window Prometheus aggregate for the whole run level (one scraped worker); "
    "NOT attributable to this request"
)
STAGE_ORDER = (
    "app",
    "guard",
    "admission",
    "placement",
    "queue",
    "hop",
    "engine",
    "ttft_decode",
    "tool_call",
    "next_agent_step",
)


class TraceError(ValueError):
    """The run directory or request id cannot be traced."""


def load_requests(run_dir: Path) -> list[dict[str, Any]]:
    path = Path(run_dir) / "requests.jsonl"
    if not path.is_file():
        raise TraceError(f"{path} not found (is this a run_scenario.py run directory?)")
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def parse_gateway_log(lines: Any, request_id: str) -> list[dict[str, Any]]:
    """JSON decision records for ``request_id`` in file order. Tolerates a logger prefix
    (``INFO:inference.gateway:``) or a kubectl timestamp before the JSON object."""
    out = []
    for line in lines:
        i = line.find("{")
        if i < 0:
            continue
        try:
            rec = json.loads(line[i:])
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("request_id") == request_id:
            out.append(rec)
    return out


def _stage(
    name: str, status: str, scope: str = "per-request", **kw: Any
) -> dict[str, Any]:
    return {
        "stage": name,
        "status": status,
        "scope": scope,
        "duration_ms": kw.pop("duration_ms", None),
        "details": kw.pop("details", {}),
        "note": kw.pop("note", ""),
    }


def _ms(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else round((b - a) * 1000.0, 1)


def _first(recs: list[dict], stage: str, key: str | None = None) -> dict | None:
    return next(
        (r for r in recs if r.get("stage") == stage and (key is None or key in r)), None
    )


def _slo(rec: dict, slos: Slos) -> dict[str, Any]:
    t = TurnResult.model_validate({"prompt": "", **rec})
    interactive = (t.workload_class or "interactive") == "interactive"
    e2e_limit = t.deadline_ms or slos.default_e2e_slo_ms
    return {
        "met": is_good(t, slos),
        "success": t.status == "completed",
        "e2e_ms": t.client_duration_ms,
        "e2e_limit_ms": e2e_limit,
        "e2e_ok": t.client_duration_ms <= e2e_limit,
        "ttft_ms": t.server_ttft_ms,
        "ttft_limit_ms": slos.interactive_ttft_slo_ms if interactive else None,
        "ttft_ok": (
            None
            if not interactive
            else (
                (t.server_ttft_ms <= slos.interactive_ttft_slo_ms)
                if t.server_ttft_ms is not None
                else None
            )
        ),
        "require_ttft": slos.require_ttft,
    }


def build_trace(
    run_dir: Path, request_id: str, gateway_log: Path | None = None
) -> dict[str, Any]:
    run_dir = Path(run_dir)
    records = load_requests(run_dir)
    rec = next((r for r in records if r.get("request_id") == request_id), None)
    if rec is None:
        raise TraceError(
            f"request_id {request_id!r} not found in {run_dir / 'requests.jsonl'}"
        )
    summary = _load_json(run_dir / "summary.json") or {}
    manifest = _load_json(run_dir / "manifest.json") or {}
    slos = Slos.model_validate(summary.get("slos") or manifest.get("slos") or {})
    hdr = rec.get("gateway_headers") or {}
    unavailable: list[str] = []

    log_recs: list[dict] | None = None
    if gateway_log is not None:
        try:
            with open(gateway_log, encoding="utf-8", errors="replace") as f:
                log_recs = parse_gateway_log(f, request_id)
        except OSError as exc:
            unavailable.append(
                f"gateway log unreadable ({type(exc).__name__}): {gateway_log}"
            )
    else:
        unavailable.append(
            "no gateway log supplied (--gateway-log): only response headers used"
        )
    recs = log_recs or []
    if log_recs is not None and not recs:
        unavailable.append(f"gateway log has no lines for request_id {request_id!r}")

    admit = _first(recs, "admit", "decision")
    place = _first(recs, "place", "chosen_worker")
    q_enter = _first(recs, "queue", "queue_enter")
    q_release = _first(recs, "queue", "queue_release")
    overflow = _first(recs, "overflow")
    reject = next((r for r in recs if "code" in r), None)
    received = next((r["received_at"] for r in recs if "received_at" in r), None)
    if recs and received is None:
        unavailable.append(
            "gateway log predates received_at/ts fields: guard/admit/place durations unavailable"
        )

    # Terminal rejection: from the log when present, else inferred from response headers.
    rejected_at = reject["stage"] if reject else None
    if rejected_at is None:
        if str(hdr.get("x-guard-decision", "")).startswith("reject"):
            rejected_at = "guard"
        elif str(hdr.get("x-admit-decision", "")).startswith("shed"):
            rejected_at = "admit"
    reached = {"guard": 0, "admit": 1, "place": 2, "queue": 3}
    cut = reached.get(rejected_at, 99)

    def not_reached(name: str, idx: int) -> dict | None:
        if idx > cut:
            return _stage(
                name, "not_reached", note=f"request rejected at {rejected_at}"
            )
        return None

    stages: list[dict] = []
    # app / replayer
    stages.append(
        _stage(
            "app",
            "observed",
            duration_ms=rec.get("client_duration_ms"),
            details={
                "source": "requests.jsonl (replayer)",
                "conversation_id": rec.get("conversation_id"),
                "agent_step": rec.get("turn_index", 0) + 1,
                "workload_class": rec.get("workload_class"),
                "tenant_id": rec.get("tenant_id"),
                "deadline_ms": rec.get("deadline_ms"),
                "force_worker": rec.get("force_worker"),
                "started_at": rec.get("started_at"),
                "ended_at": rec.get("ended_at"),
            },
            note="duration is client-observed end-to-end",
        )
    )
    # guard
    guard_hdr = hdr.get("x-guard-decision")
    if reject and reject["stage"] == "guard":
        stages.append(
            _stage(
                "guard",
                "rejected",
                details={"code": reject.get("code"), "reason": reject.get("reason")},
            )
        )
    else:
        stages.append(
            _stage(
                "guard",
                "observed" if guard_hdr or admit or place else "unavailable",
                details={
                    "decision": guard_hdr or ("allow" if (admit or place) else None)
                },
                note="guard is not timed separately; see admission (guard + quota + admit)",
            )
        )
    # admission
    if s := not_reached("admission", 1):
        stages.append(s)
    elif reject and reject["stage"] == "admit":
        stages.append(
            _stage(
                "admission",
                "rejected",
                details={
                    "code": reject.get("code"),
                    "reason": reject.get("reason"),
                    "admission_inputs": reject.get("admission_inputs"),
                    "never_overflow": reject.get("never_overflow"),
                },
                duration_ms=_ms(received, reject.get("ts")),
                note="duration = gateway receive -> shed (includes guard)",
            )
        )
    elif admit:
        stages.append(
            _stage(
                "admission",
                "observed",
                duration_ms=_ms(received, admit.get("ts")),
                details={
                    "decision": admit["decision"],
                    "x-admit-decision": hdr.get("x-admit-decision"),
                    "admission_inputs": admit.get("admission_inputs"),
                },
                note="duration = gateway receive -> admit accept (guard + tenant quota + "
                "should_shed; not separable)",
            )
        )
    else:
        stages.append(
            _stage(
                "admission",
                "partial" if hdr.get("x-admit-decision") else "unavailable",
                details={"x-admit-decision": hdr.get("x-admit-decision")},
                note="no gateway log: decision from response header only, no inputs/duration",
            )
        )
    # placement
    if s := not_reached("placement", 2):
        stages.append(s)
    elif reject and reject["stage"] == "place":
        stages.append(
            _stage(
                "placement",
                "rejected",
                details={"code": reject.get("code"), "reason": reject.get("reason")},
            )
        )
    elif place:
        fields = (
            "chosen_worker",
            "prior_worker",
            "placement_policy",
            "placement_reason",
            "estimated_reusable_tokens",
            "intended_action",
            "snapshot_age",
            "fallback",
        )
        stages.append(
            _stage(
                "placement",
                "observed",
                duration_ms=_ms(admit.get("ts") if admit else None, place.get("ts")),
                details={k: place.get(k) for k in fields}
                | {"prefix_id": place.get("prefix_id")},
                note="duration = admit accept -> placement decision",
            )
        )
    elif hdr.get("x-place-decision"):
        stages.append(
            _stage(
                "placement",
                "partial",
                details={
                    "chosen_worker": hdr.get("x-place-decision"),
                    "placement_policy": hdr.get("x-placement-policy"),
                    "placement_reason": hdr.get("x-placement-reason"),
                    "intended_action": hdr.get("x-intended-action"),
                },
                note="from response headers only (no prior worker/belief age/duration)",
            )
        )
    else:
        stages.append(
            _stage(
                "placement", "unavailable", note="no gateway log or placement headers"
            )
        )
    # queue
    if s := not_reached("queue", 3):
        stages.append(s)
    elif reject and reject["stage"] == "queue":
        stages.append(
            _stage(
                "queue",
                "rejected",
                duration_ms=reject.get("queue_wait_ms"),
                details={"code": reject.get("code"), "reason": reject.get("reason")},
            )
        )
    elif q_enter:
        stages.append(
            _stage(
                "queue",
                "observed",
                duration_ms=q_enter.get("queue_wait_ms"),
                details={
                    "queue_enter": q_enter.get("queue_enter"),
                    "queue_dispatch": q_enter.get("queue_dispatch"),
                    "x-queue-decision": hdr.get("x-queue-decision"),
                },
                note="duration = gateway queue wait",
            )
        )
    elif hdr.get("x-queue-wait-ms") is not None:
        try:
            wait = float(hdr["x-queue-wait-ms"])
        except ValueError:
            wait = None
        stages.append(
            _stage(
                "queue",
                "partial",
                duration_ms=wait,
                details={"x-queue-decision": hdr.get("x-queue-decision")},
                note="from response headers only",
            )
        )
    else:
        stages.append(
            _stage("queue", "unavailable", note="no gateway log or queue headers")
        )
    # hop (only a logged, confirmed transfer counts)
    intended = (place or {}).get("intended_action") or hdr.get("x-intended-action")
    hops = [r for r in recs if r.get("stage") == "hop"]
    done = next((r for r in hops if r.get("hop_result") in CONFIRMED_HOP_RESULTS), None)
    if done:
        stages.append(
            _stage(
                "hop",
                "confirmed",
                duration_ms=done.get("transfer_ms"),
                details={k: v for k, v in done.items() if k not in ("stage",)},
                note="confirmed transfer record from the gateway hop log (#133 contract)",
            )
        )
    elif hops:
        stages.append(
            _stage(
                "hop",
                "attempted_not_confirmed",
                details={
                    "intended_action": intended,
                    "hop_result": hops[-1].get("hop_result"),
                },
                note="a hop was attempted but no confirmed transfer: NOT a KV hop",
            )
        )
    else:
        stages.append(
            _stage(
                "hop",
                "not_attempted",
                details={"intended_action": intended},
                note="not attempted (#133): intended_action is the router's belief, never proof; "
                "a worker change, prefix match or lower latency is not evidence of a hop",
            )
        )
    # engine (gateway proxy per-request; vLLM waiting/running only as window context)
    if s := not_reached("engine", 3):
        stages.append(s)
    else:
        proxy_ms = _ms(
            (q_enter or {}).get("queue_dispatch"),
            (q_release or {}).get("queue_release"),
        )
        details: dict[str, Any] = {}
        window = None
        for lv in summary.get("levels") or []:
            if lv.get("offered_concurrency") == rec.get("offered_concurrency"):
                window = (lv.get("prometheus_window") or {}).get("delta")
        if window:
            details["vllm_window_aggregate"] = {
                k: window.get(k) for k in WINDOW_KEYS if k in window
            }
        stages.append(
            _stage(
                "engine",
                "observed" if proxy_ms is not None else "unavailable",
                duration_ms=proxy_ms,
                details=details,
                note="duration = gateway dispatch -> release for THIS request (vLLM waiting + "
                "prefill + decode as seen by the gateway; not separable per request). "
                + (
                    "vllm_window_aggregate is " + WINDOW_NOTE
                    if window
                    else "no prometheus_window in summary.json: vLLM waiting/running evidence "
                    "unavailable (run with --metrics-url; it is window-level at best)"
                ),
            )
        )
        if window:
            stages[-1]["scope"] = "per-request + window"
    # TTFT / decode
    ttft, e2e = rec.get("server_ttft_ms"), rec.get("client_duration_ms")
    stages.append(
        _stage(
            "ttft_decode",
            "observed" if ttft is not None else "partial",
            duration_ms=e2e,
            details={
                "ttft_ms": ttft,
                "decode_ms_derived": (
                    round(e2e - ttft, 1)
                    if ttft is not None and e2e is not None
                    else None
                ),
                "tokens_in": rec.get("tokens_in"),
                "tokens_out": rec.get("tokens_out"),
            },
            note="ttft/e2e are client-observed; decode = e2e - ttft (derived). "
            + (
                ""
                if ttft is not None
                else "TTFT unmeasured (non-streaming or failed request)."
            ),
        )
    )
    # tool call + next step
    stages.append(
        _stage(
            "tool_call",
            "unavailable",
            note="requests.jsonl records no tool execution (gateway_chat replays model calls "
            "only; app_runs tool spans live in app run events/traces)",
        )
    )
    conv = sorted(
        (r for r in records if r.get("conversation_id") == rec.get("conversation_id")),
        key=lambda r: r.get("turn_index", 0),
    )
    nxt = next(
        (r for r in conv if r.get("turn_index", 0) > rec.get("turn_index", 0)), None
    )
    if nxt:
        gap = _ms(rec.get("ended_at"), nxt.get("started_at"))
        stages.append(
            _stage(
                "next_agent_step",
                "observed",
                duration_ms=gap,
                details={
                    "request_id": nxt.get("request_id"),
                    "agent_step": nxt.get("turn_index", 0) + 1,
                    "status": nxt.get("status"),
                    "x-place-decision": (nxt.get("gateway_headers") or {}).get(
                        "x-place-decision"
                    ),
                    "x-intended-action": (nxt.get("gateway_headers") or {}).get(
                        "x-intended-action"
                    ),
                },
                note="duration = this request ended -> next step started (replayer delay + any "
                "tool time not recorded separately)",
            )
        )
    else:
        stages.append(
            _stage(
                "next_agent_step",
                "not_applicable",
                note="last recorded step in this conversation",
            )
        )
    if overflow:
        stages.append(
            _stage(
                "overflow",
                "observed",
                details={
                    k: overflow.get(k)
                    for k in (
                        "reason",
                        "outcome",
                        "overflow_provider",
                        "overflow_model",
                        "skip",
                    )
                },
            )
        )

    slo = _slo(rec, slos)
    chosen = (place or {}).get("chosen_worker") or hdr.get("x-place-decision")
    return {
        "request_id": request_id,
        "conversation_id": rec.get("conversation_id"),
        "agent_step": rec.get("turn_index", 0) + 1,
        "status": rec.get("status"),
        "chosen_worker": chosen,
        "placement_policy": (place or {}).get("placement_policy")
        or hdr.get("x-placement-policy"),
        "placement_reason": (place or {}).get("placement_reason")
        or hdr.get("x-placement-reason"),
        "intended_action": intended,
        "tokens_in": rec.get("tokens_in"),
        "tokens_out": rec.get("tokens_out"),
        "slo": slo,
        "stages": stages,
        "unavailable": unavailable,
        "sources": {
            "run_dir": str(run_dir),
            "gateway_log": str(gateway_log) if gateway_log else None,
            "gateway_log_lines": None if log_recs is None else len(log_recs),
            "manifest_scenario": (manifest.get("scenario") or {}).get("name"),
            "topology": manifest.get("topology"),
        },
        "evidence_scope": "per-request unless a stage is labeled window; window values are "
        "aggregates and never this request's own numbers",
    }


def _fmt(v: Any) -> str:
    return "n/a" if v is None else (f"{v:g}" if isinstance(v, float) else str(v))


def render_text(trace: dict[str, Any]) -> str:
    slo = trace["slo"]
    lines = [
        f"Request {trace['request_id']}  conversation {trace['conversation_id']}  "
        f"agent_step {trace['agent_step']}  status {trace['status']}",
        f"chosen worker {_fmt(trace['chosen_worker'])}  policy {_fmt(trace['placement_policy'])}  "
        f"reason {_fmt(trace['placement_reason'])}  "
        f"intended_action {_fmt(trace['intended_action'])}",
        f"tokens in/out {_fmt(trace['tokens_in'])}/{_fmt(trace['tokens_out'])}  "
        f"SLO {'MET' if slo['met'] else 'NOT MET'} "
        f"(success={slo['success']}, e2e {_fmt(slo['e2e_ms'])}ms vs {_fmt(slo['e2e_limit_ms'])}ms "
        f"ok={slo['e2e_ok']}, ttft {_fmt(slo['ttft_ms'])}ms vs {_fmt(slo['ttft_limit_ms'])}ms "
        f"ok={_fmt(slo['ttft_ok'])})",
        "",
        "Stage timeline",
    ]
    for i, st in enumerate(trace["stages"], 1):
        dur = "" if st["duration_ms"] is None else f"{st['duration_ms']:g} ms"
        lines.append(
            f"{i:>2}. {st['stage']:<16} {st['status']:<24} [{st['scope']}] {dur}"
        )
        for k, v in st["details"].items():
            if v is not None:
                shown = (
                    json.dumps(v, sort_keys=True) if isinstance(v, (dict, list)) else v
                )
                lines.append(f"      {k}: {shown}")
        if st["note"]:
            lines.append(f"      note: {st['note']}")
    if trace["unavailable"]:
        lines += ["", "Unavailable / caveats"] + [
            f"  - {u}" for u in trace["unavailable"]
        ]
    lines += ["", f"Evidence scope: {trace['evidence_scope']}"]
    return "\n".join(lines) + "\n"
