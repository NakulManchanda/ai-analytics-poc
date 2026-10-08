"""Generate experiments/123_evidence.ipynb (diff-friendly: no outputs, stable ids, no results).

Run: make evidence-notebook [RUNS_JSON=path WRITEUP_DIR=path HTML=path]
"""

from __future__ import annotations

import json
from pathlib import Path

MD = "markdown"

SETUP = '''\
import json, os, sys
from pathlib import Path

ROOT = next(p for p in [Path.cwd(), *Path.cwd().parents] if (p / "experiments" / "analysis").is_dir())
sys.path.insert(0, str(ROOT))
from experiments.analysis import (breakdown, e2_prefix_reuse, e3_compare, e4_compare,
                                  load_run, memory_proof, queue_proof, sweep_curve, ttft_by_turn)
from experiments.analysis.display import show as plain_show, flatten
from html import escape
try:
    from IPython import get_ipython
    from IPython.display import HTML, Markdown, display
except ImportError:
    get_ipython = lambda: None
    HTML = Markdown = str
    display = print


def show(title, result):
    """Readable notebook tables; retain CLI output when no notebook kernel is active."""
    shell = get_ipython()
    if shell is None or not hasattr(shell, "kernel"):
        return plain_show(title, result)

    def value(v):
        if v is None:
            return "unavailable"
        if isinstance(v, float):
            return f"{v:,.2f}"
        return str(v)

    def table(rows):
        keys = list(dict.fromkeys(k for row in rows for k in row))
        head = "".join(f"<th>{escape(k)}</th>" for k in keys)
        body = "".join("<tr>" + "".join(f"<td>{escape(value(row.get(k)))}</td>" for k in keys) + "</tr>" for row in rows)
        return f"<div style='overflow-x:auto'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"

    def render(obj):
        if isinstance(obj, dict):
            scalar = [{"metric": k, "value": v} for k, v in obj.items() if not isinstance(v, (dict, list))]
            out = table(scalar) if scalar else ""
            for key, v in obj.items():
                if isinstance(v, (dict, list)) and v:
                    out += f"<h4>{escape(key)}</h4>" + render(v)
            return out
        if isinstance(obj, list):
            if all(isinstance(v, dict) for v in obj):
                return table([flatten(v) for v in obj])
            return "<ul>" + "".join(f"<li>{escape(value(v))}</li>" for v in obj) + "</ul>"
        return f"<p>{escape(value(obj))}</p>"

    display(HTML(f"<h3>{escape(title)}</h3>" + render(result)))


def writeup(experiment):
    """Optionally include a session's Markdown talk track without committing local results."""
    directory = os.environ.get("EVIDENCE_WRITEUP_DIR")
    if directory:
        path = Path(directory) / f"{experiment.lower()}.md"
        if path.is_file():
            display(Markdown(f"## Observed results and interpretation — {experiment}"))
            display(Markdown(path.read_text(encoding="utf-8")))

# Fill in pulled run directories (metrics/inference/<run-id>/<scenario_strategy_ts>/), or set the
# env var EVIDENCE_RUNS_JSON to a JSON object with the same keys. None = not provided.
RUNS = {
    "E0_SWEEP": None,          # run dir from an offered-load sweep (sweep.csv/json)
    "E0_CAPACITY": None,       # dir containing capacity_summary.json (#120 capacity runner)
    "E1_WARMUP_COLD": None,    # dir containing warmup_summary.json right after a worker restart
    "E1_WARMUP_WARM": None,    # dir containing warmup_summary.json after declared warm-up
    "E2_COLD": None, "E2_REUSED": None,
    "E3_LEAST_LOADED": None, "E3_PREFIX_THEN_LOAD": None,
    "E4_ADMISSION_OFF": None, "E4_ADMISSION_ON": None,
    "E5_RUN": None,            # any gateway run dir (recompute control); shown by turn and worker
    "MEMORY_RANGE": None,      # metrics/inference/<run-id>/prometheus_range
    "QUEUE_RANGE": None,       # metrics/inference/<run-id>/prometheus_range (gateway queue vs vLLM waiting)
}
RUNS.update(json.loads(os.environ.get("EVIDENCE_RUNS_JSON", "{}")))


def need(*keys):
    """Return the Paths for keys, or print why the experiment cell is skipped."""
    missing = [k for k in keys if not RUNS.get(k)]
    if missing:
        print(f"run directory not provided: set RUNS{missing}; skipping this experiment")
        return None
    return [Path(RUNS[k]) for k in keys]


'''

CHARTS = """\
# Chart helpers: missing values stay missing; captions preserve evidence scope.
from experiments.analysis.runs import latency_stats

BLUE, ORANGE, AQUA, INK, MUTED = "#2a78d6", "#eb6834", "#1baf7a", "#0b0b0b", "#52514e"
COLORS = [BLUE, ORANGE, AQUA]
try:
    import matplotlib
    if get_ipython() is None:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "axes.grid.axis": "y",
        "axes.axisbelow": True, "grid.color": "#e6e5e1", "grid.linewidth": 0.8, "axes.edgecolor": "#c9c8c2",
        "text.color": INK, "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED,
        "figure.facecolor": "#fcfcfb", "axes.facecolor": "#fcfcfb", "figure.dpi": 110,
    })
except ImportError:
    plt = None
    print("matplotlib not installed: charts are skipped, tables only")


def plot_sweep(curve):
    if plt is None or not curve["rows"]:
        return
    rows = curve["rows"]
    x = [r["offered_concurrency"] for r in rows]
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.8))
    for key, label in (("completed_rps", "completed throughput"), ("good_rps", "SLO-qualified goodput")):
        axes[0].plot(x, [r[key] for r in rows], marker="o", label=label)
    axes[0].set_ylabel("requests / second")
    axes[0].set_title("Completions vs useful completions", loc="left")
    for key, label in (("ttft_p95_ms", "TTFT p95"), ("e2e_p95_ms", "E2E p95")):
        axes[1].plot(x, [r[key] for r in rows], marker="o", label=label)
    axes[1].set_ylabel("client latency (ms)")
    axes[1].set_title("Latency cost of offered load", loc="left")
    for ax in axes:
        ax.set_xlabel("offered concurrency")
        ax.set_xticks(x)
        ax.legend(frameon=False, fontsize=8)
    plt.tight_layout()
    plt.show()


def worker_name(url):
    return {"18001": "worker A", "18002": "worker B"}.get(str(url).rstrip("/").rsplit(":", 1)[-1], str(url))


def bars(ax, groups, series, ylabel, title):
    n = len(series)
    w = 0.8 / n
    for i, (label, vals) in enumerate(series.items()):
        vals = [float("nan") if v is None else v for v in vals]
        xs = [g + (i - (n - 1) / 2) * w for g in range(len(groups))]
        rects = ax.bar(xs, vals, w * 0.92, label=label, color=COLORS[i % len(COLORS)])
        ax.bar_label(rects, labels=["n/a" if v != v else f"{v:,.0f}" for v in vals], fontsize=8, color=MUTED, padding=2)
    ax.set_xticks(range(len(groups)))
    ax.set_xticklabels(groups)
    ax.set_ylabel(ylabel)
    ax.set_title(title, loc="left", fontsize=10, color=INK)
    ax.legend(frameon=False, fontsize=8)


def turn_stat(run, stat):
    rows = ttft_by_turn(run)["rows"]
    turns = sorted(rows, key=lambda t: int(t.split("_")[1]))
    return [t.replace("turn_", "turn ") for t in turns], [rows[t]["ttft_ms"][stat] for t in turns]


def compare_by_turn(title, a_label, a_run, b_label, b_run):
    if plt is None:
        return
    ra, rb = ttft_by_turn(a_run)["rows"], ttft_by_turn(b_run)["rows"]
    keys = sorted(set(ra) | set(rb), key=lambda t: int(t.split("_")[1]))
    turns = [t.replace("turn_", "turn ") for t in keys]
    a50 = [(ra.get(t, {}).get("ttft_ms") or {}).get("p50") for t in keys]
    b50 = [(rb.get(t, {}).get("ttft_ms") or {}).get("p50") for t in keys]
    sa, sb = (latency_stats(r.select())["ttft_ms"] for r in (a_run, b_run))
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 3.8), gridspec_kw={"width_ratios": [3, 1.6]})
    bars(ax1, turns, {a_label: a50, b_label: b50}, "client TTFT p50 (ms)", f"{title}: TTFT p50 by turn")
    bars(ax2, ["p50", "p95"], {a_label: [sa["p50"], sa["p95"]], b_label: [sb["p50"], sb["p95"]]},
         "client TTFT (ms)", "all turns")
    fig.text(0.01, -0.02, "Replay TTFT includes the client transport path. Missing observations are not zero.", fontsize=8, color=MUTED)
    plt.tight_layout()
    plt.show()


def compare_breakdown(title, by, a_label, a_run, b_label, b_run, stat="p95"):
    if plt is None:
        return
    ra, rb = breakdown(a_run, by)["rows"], breakdown(b_run, by)["rows"]
    groups = sorted(set(ra) | set(rb))
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 3.6))
    get = lambda rows, g, k: (rows.get(g, {}).get("ttft_ms") or {}).get(k) if k != "n" else rows.get(g, {}).get("requests", 0)
    bars(ax1, groups, {a_label: [get(ra, g, stat) for g in groups], b_label: [get(rb, g, stat) for g in groups]},
         f"client TTFT {stat} (ms)", f"{title}: TTFT {stat} by {by}")
    bars(ax2, groups, {a_label: [get(ra, g, "n") for g in groups], b_label: [get(rb, g, "n") for g in groups]},
         "requests", f"requests by {by}")
    plt.tight_layout()
    plt.show()


def plot_capacity(summary):
    if plt is None:
        return
    sweep = summary.get("live_sweep", [])
    if not sweep:
        return print("No live capacity sweep observations available")
    ctxs = sorted({x["context_length_target"] for x in sweep})
    cap = (summary.get("configured_ceiling") or {}).get("value")
    fig, axes = plt.subplots(1, len(ctxs), figsize=(11, 3.4), sharey=True, squeeze=False)
    axes = axes[0]
    for ax, c in zip(axes, ctxs):
        rows = [x for x in sweep if x["context_length_target"] == c]
        xs = [x["concurrency"] for x in rows]
        ax.plot(xs, [x["peak_running"] for x in rows], marker="o", color=BLUE, label="peak running")
        ax.plot(xs, [x["peak_waiting"] for x in rows], marker="o", color=ORANGE, label="peak waiting")
        if cap:
            ax.axhline(cap, color=MUTED, linestyle="--", linewidth=1)
        ax.set_xticks(xs)
        ax.set_title(f"{c}-token context", loc="left", fontsize=10, color=INK)
        ax.set_xlabel("offered concurrency")
    axes[0].set_ylabel("requests in engine (peak)")
    axes[0].legend(frameon=False, fontsize=8)
    fig.suptitle(f"E0 capacity: observed running and waiting; configured ceiling={cap}", x=0.01, ha="left", fontsize=10)
    plt.tight_layout()
    plt.show()


def plot_memory(res):
    if plt is None:
        return
    ts, ser = res["timestamps"], res["series"]
    if not ts:
        return print("No memory range samples available")
    t = [(x - ts[0]) / 60 for x in ts]
    active = [i for i in range(len(ts)) if any((v[i] or 0) > 0 for k, v in ser.items() if "kv_cache" in k or "requests_" in k)]
    lo, hi = (max(min(active) - 8, 0), min(max(active) + 8, len(ts))) if active else (0, len(ts))
    t, ser = t[lo:hi], {k: v[lo:hi] for k, v in ser.items()}
    panels = [
        ("HBM used (GiB)", "dcgm_fb_used_mib", 1 / 1024),
        ("KV cache usage (%)", "vllm_kv_cache_usage", 100),
        ("requests running", "vllm_requests_running", 1),
        ("requests waiting", "vllm_requests_waiting", 1),
    ]
    fig, axes = plt.subplots(len(panels), 1, figsize=(11, 7.5), sharex=True)
    for ax, (label, prefix, k) in zip(axes, panels):
        lines = [(name, vals) for name, vals in ser.items() if name.split(":")[0] == prefix]
        for i, (name, vals) in enumerate(lines):
            if vals is not None:
                ax.plot(t, [None if v is None else v * k for v in vals], color=COLORS[i % len(COLORS)], label=name, linewidth=1.6)
        ax.set_ylabel(label)
        if lines:
            ax.legend(frameon=False, fontsize=8, loc="upper left")
    axes[0].set_title("Memory proof (WINDOW-level samples): HBM, KV, running and waiting, zoomed to the active part of the supplied window", loc="left", fontsize=10, color=INK)
    axes[-1].set_xlabel("minutes since window start (idle lead-in and tail trimmed)")
    plt.tight_layout()
    plt.show()


def plot_queue(res):
    if plt is None:
        return
    ts, ser = res["timestamps"], res["series"]
    if not ts:
        return print("No queue range samples available")
    t = [(x - ts[0]) / 60 for x in ts]
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 5.5), sharex=True)
    q_lines = [(name, vals) for name, vals in ser.items() if "orch_replica_queue_depth" in name]
    w_lines = [(name, vals) for name, vals in ser.items() if "vllm_requests_waiting" in name]
    for i, (name, vals) in enumerate(q_lines):
        if vals is not None:
            ax1.plot(t, vals, color=COLORS[i % len(COLORS)], label=f"gateway queue: {name.split(':', 1)[-1]}", linewidth=1.6)
    for i, (name, vals) in enumerate(w_lines):
        if vals is not None:
            ax1.plot(t, vals, "--", color=ORANGE if i == 0 else MUTED, label=f"vLLM waiting: {name.split(':', 1)[-1]}", linewidth=1.6)
    ax1.set_ylabel("requests")
    ax1.set_title("Part 5 scheduler proof: Gateway replica queue vs vLLM waiting requests", loc="left", fontsize=10, color=INK)
    ax1.legend(frameon=False, fontsize=8, loc="upper left")

    r_lines = [(name, vals) for name, vals in ser.items() if "vllm_requests_running" in name]
    p_lines = [(name, vals) for name, vals in ser.items() if "vllm_preemption_rate" in name]
    for i, (name, vals) in enumerate(r_lines):
        if vals is not None:
            ax2.plot(t, vals, color=COLORS[i % len(COLORS)], label=f"vLLM running: {name.split(':', 1)[-1]}", linewidth=1.6)
    for i, (name, vals) in enumerate(p_lines):
        if vals is not None:
            ax2.plot(t, vals, ":", color=ORANGE, label=f"preemptions/s: {name.split(':', 1)[-1]}", linewidth=1.6)
    ax2.set_ylabel("running / rate")
    ax2.set_title("vLLM active running requests and preemption rate", loc="left", fontsize=10, color=INK)
    ax2.legend(frameon=False, fontsize=8, loc="upper left")
    ax2.set_xlabel("minutes since window start")
    plt.tight_layout()
    plt.show()
"""

E0 = """\
if (p := need("E0_SWEEP")):
    run = load_run(p[0])
    curve = sweep_curve(run)
    show("Recorded latency SLOs (ms)", run.manifest.get("slos") or {"note": "SLO metadata unavailable"})
    if curve["rows"] and all(r[curve["knee_metric"]] == 0 for r in curve["rows"]):
        curve = {**curve, "knee_offered_concurrency": None,
                 "warnings": [*curve["warnings"], "All measured goodput values are zero: no useful goodput knee can be inferred."]}
    show("E0 offered load vs throughput vs goodput (knee)", curve)
    plot_sweep(curve)
if (p := need("E0_CAPACITY")):
    cap = json.loads((p[0] / "capacity_summary.json").read_text())
    print("E0 capacity runner (synthetic, preliminary): first limiter =", cap.get("first_limiter"), "| limiter by context =", cap.get("limiters_by_context"))
    plot_capacity(cap)
writeup("E0")
"""

E1 = """\
if (p := need("E1_WARMUP_COLD", "E1_WARMUP_WARM")):
    cold, warm = (json.loads((d / "warmup_summary.json").read_text()) for d in p)
    groups, first, steady = [], [], []
    for worker in sorted(set(cold) | set(warm)):
        for name, run in (("cold arm", cold), ("declared-warm arm", warm)):
            if worker in run:
                groups.append(f"{worker_name(worker)}\\n{name}")
                first.append(run[worker]["cold_ttft_ms"])
                steady.append(run[worker]["warm_ttft_p50_ms"])
    if plt:
        fig, ax = plt.subplots(figsize=(9, 3.6))
        bars(ax, groups, {"first request": first, "subsequent requests, p50": steady}, "client TTFT (ms)",
             "E1: first request vs subsequent requests (client TTFT)")
        plt.tight_layout()
        plt.show()
    show("E1 warmup observations", {"cold": cold, "declared_warm": warm})
writeup("E1")
"""

E2 = """\
if (p := need("E2_COLD", "E2_REUSED")):
    cold, reused = load_run(p[0]), load_run(p[1])
    compare_by_turn("E2 prefix reuse", "cold cache", cold, "reused cache", reused)
    show("E2 prefix reuse (cold vs reused)", e2_prefix_reuse(cold, reused))
writeup("E2")
"""

E3 = """\
if (p := need("E3_LEAST_LOADED", "E3_PREFIX_THEN_LOAD")):
    ll, ptl = load_run(p[0]), load_run(p[1])
    compare_by_turn("E3 routing", "least_loaded", ll, "prefix_then_load", ptl)
    compare_breakdown("E3 routing", "worker", "least_loaded", ll, "prefix_then_load", ptl)
    show("E3 least_loaded (a) vs prefix_then_load (b)", e3_compare(ll, ptl))
writeup("E3")
"""

E4 = """\
if (p := need("E4_ADMISSION_OFF", "E4_ADMISSION_ON")):
    off, on = load_run(p[0]), load_run(p[1])
    compare_breakdown("E4 admission", "workload_class", "admission off", off, "admission on", on)
    show("E4 admission off (a) vs on (b)", e4_compare(off, on))
"""

E5 = """\
if (p := need("E5_RUN")):
    run = load_run(p[0])
    if plt:
        turns, p50 = turn_stat(run, "p50")
        _, p95 = turn_stat(run, "p95")
        fig, ax = plt.subplots(figsize=(8, 3.4))
        bars(ax, turns, {"p50": p50, "p95": p95}, "client TTFT (ms)", "E5 TTFT by turn")
        plt.tight_layout()
        plt.show()
    show("E5 TTFT by turn", ttft_by_turn(run))
    show("E5 latency by worker", breakdown(run, "worker"))
"""

MEM = """\
if (p := need("MEMORY_RANGE")):
    res = memory_proof(p[0])
    plot_memory(res)
    print("flags:", res["flags"] or "none", "| warnings:", res["warnings"] or "none")
"""

QUEUE = """\
if (p := need("QUEUE_RANGE")):
    res = queue_proof(p[0])
    show("Gateway queue vs vLLM waiting summary", res["summary"])
    plot_queue(res)
    if res["warnings"]:
        print("warnings:", res["warnings"])
"""


OVERVIEW = """## What these experiments are testing

Two vLLM workers serve the same model and keep separate prefix caches. The experiment
sequence separates capacity, restart effects, cache reuse, routing and overload protection.
The descriptions below follow the **runbook/playbook definitions**; older testing-guide
sections use different names for E0 and E2.

| Experiment | Question | What changes | Main evidence |
|---|---|---|---|
| E0 — capacity | What saturates first as offered load increases? | Concurrency and synthetic context length | Throughput, goodput, latency, running/waiting and KV usage |
| E1 — restart / warmup | Is the first request after readiness slower than later requests? | Immediately after restart vs declared-warm state | First-request TTFT, subsequent-request percentiles; startup logs separately |
| E2 — prefix reuse | Does an already cached prompt prefix reduce latency? | First replay after reset vs identical replay without reset | TTFT by turn, manifest parity, window-level cache-hit deltas |
| E3 — routing | Does preferring the worker holding the prefix beat spreading load? | `least_loaded` vs `prefix_then_load` | TTFT by turn and worker, tail latency, placement and reuse evidence |
| E4 — admission | Does rejecting work early preserve useful interactive service under overload? | Capacity/deadline shedding off vs on | Interactive goodput, tail latency, sheds, fairness and starvation |
| E5 — locality / recompute control | What does recomputing on another worker cost? | Local reuse vs recompute vs independently warmed destination | Latency and cache evidence at several prefix sizes; real KV transfer needs #133 |

**Terms used in the charts.** TTFT means time to first generated content token; E2E means
elapsed time until the request completes. p50 is the median; p95 is a tail percentile
(about 95% of observations are at or below it). Offered concurrency is the replay's
in-flight workload limit, not a guaranteed count of simultaneously executing GPU requests.
Prefill processes the input prompt; decode generates output tokens. The KV cache stores
attention keys and values; prefix reuse avoids recomputing matching cached prompt tokens.
HBM is GPU memory. A latency SLO is the threshold defining a useful completion.

**How to read this report.** Descriptions are experimental intent, not findings. Supplied
runs produce charts and tables; optional session writeups explain measured results and
caveats. Missing runs are skipped, so a description alone does not prove an experiment ran.
Per-request replay latency includes the transport path; Prometheus samples and cache-hit
ratios describe a whole window. Check parity and repeat runs before making a causal or
statistical claim.

**Expected vs observed.** Each section states the hypothesis separately from the supplied
run's observations. Expected behavior is not an acceptance result. The E0–E3 session
writeups contain the observed findings and caveats; E4/E5 have no observations in this
session because those runs have not been supplied.

**Next validation:** we will rerun the experiments once more from scratch, with fresh
setup, recorded pre-flight checks and deliberate cache resets for each comparison.
The current observations are preliminary. The rerun should check whether the patterns
repeat; it must not assume the same numerical results. Keep both sessions' artifacts
and compare them rather than replacing the original evidence.

**Repository sources:** `docs/inference-experiments/inference-experiments-reference.md`
(definitions), `inference-run-playbook.md` and `inference-session-runbook.md` (procedure),
`inference-testing-guide.md` (methodology and policy semantics), `inference-evidence-index.md`
(proof requirements), and this folder's `README.md` (observability and scrape caveats).
"""

CONTEXT = {
    "E0": """**Question:** what limits service first: scheduler slots, KV memory, prefill work or queueing?

**Method:** combine a synthetic sweep sent directly to one worker with an application-shaped
multi-turn replay through the gateway. Sweep offered concurrency; the capacity runner also
varies context length. These are different workloads and should be interpreted separately.

**Read the charts:** compare completed throughput and SLO-qualified goodput with p95 latency.
The capacity plot shows observed peak running/waiting against the configured ceiling. Correlate
with KV usage and preemptions before identifying a limiter. A throughput peak alone cannot
identify the saturated resource; a short, ascending sweep also changes cache state.

""",
    "E1": """**Question:** how much does the first request after a worker restart differ from steady service?

**Method:** restart the workers, wait for readiness, measure the first request and subsequent
requests, then repeat in the declared-warm state after an idle interval. This experiment
uses direct worker requests, so its path differs from the gateway replays.

**Read the chart:** compare each arm's first-request TTFT with its subsequent-request p50.
Full restart recovery, weight loading and CUDA graph capture require startup logs and readiness
timing; they are not measured by these bars. A small or absent gap does not prove the restart
failed: the engine may do expensive initialization before it becomes Ready.

""",
    "E2": """**Question:** does an already populated prefix cache make the same trace faster?

**Method:** reset both workers, declare warmup, replay the trace once, then replay it again
without restarting. Keep scenario, policy, model, engine flags and SLOs fixed. If the replay
requests a policy override, the gateway experiment controls must be enabled for both arms.

**Read the charts:** focus on the first turn of each conversation, then compare later turns
and overall p50/p95. The cold arm warms its own cache as it runs, so this is first pass vs
reused cache, not disabled caching vs enabled caching. Cache-hit percentages are aggregate
window evidence and may include only one worker; they cannot identify a particular hit.

""",
    "E3": """**Question:** does prefix affinity save enough prefill work to outweigh queueing on a preferred worker?

**Method:** replay the same multi-turn taxi trace with `least_loaded` (spread by current load)
and `prefix_then_load` (prefer a reusable prefix when overlap and headroom allow, otherwise
fall back to load). Reset both workers before each arm and verify the requested policy was
applied. Only the routing policy should differ.

**Read the charts:** inspect first-turn and later-turn TTFT separately, overall tail latency
and worker request counts. A policy can improve later turns while worsening the first burst.
For the headline trace, the roughly 270-token system prefix becomes a smaller share as history
grows: the 0.8 overlap gate is expected to produce `prefix_overlap_low` from turn 2 onward.
This policy measures system-prefix affinity, not history-aware conversation stickiness.
The optional roughly 6k-token synthetic-prefix variant is a separate treatment.

""",
    "E4": """**Question:** can early admission shedding protect interactive goodput during overload?

**Method:** use the same mixed interactive/noisy/batch trace with capacity/deadline admission
off and on. Enable experiment controls and the tenant allowlist, and ensure the offered
load actually overloads the workers. Admission off still enforces tenant quotas.

**Read the evidence:** compare class-specific latency and useful completions, shed reasons,
queue timeouts and fairness. Lower latency among survivors is insufficient if interactive
goodput falls or batch work starves. Proving shed requests never reached the engine needs
correlated gateway and worker evidence; these latency charts alone do not establish it.

""",
    "E5": """**Question:** how does reusing a prefix locally compare with recomputing it on another worker?

**Method:** use separate labelled controls across prefix sizes: A-to-A local reuse, A-to-B
recompute without transfer, and a B destination warmed independently. Check exact token
counts in manifests rather than relying on size labels. Reset caches between independent cases.

**Read the evidence:** the current cell shows one supplied run's turn/worker latency, not a
multi-size crossover analysis. A separately warmed B cache is a destination hit, not transferred
KV. Real cross-worker KV transfer and its crossover remain dependent on #133 and explicit
transfer/reuse proof; a worker change or latency drop cannot prove a hop.

""",
    "MEM": """**Question:** do memory usage and scheduler activity support the claimed bottleneck?

**Method:** align HBM allocation, KV usage and running/waiting series over a supplied
Prometheus range. This supports E0 or E4; it is not a separate numbered experiment.

**Read the chart:** flat HBM can reflect preallocated vLLM memory while KV occupancy changes.
All available series are shown separately; duplicate scrape sources can disagree and should
not be summed. Samples can miss short bursts. The supplied range may include several workloads;
restrict the time window before attributing a peak to one experiment.

""",
    "QUEUE": """**Question:** where does the engine scheduler sit vs the gateway admit/place/queue stages?

**Method:** align gateway replica queue depth (`orch_replica_queue_depth` by worker and class) and
queue wait (`orch_queue_wait_p95`) against vLLM waiting (`vllm:num_requests_waiting`), running
(`vllm:num_requests_running`) and preemptions (`vllm:num_preemptions_total`) over the run window.

**Read the chart:** under overload, gateway queues absorb excess work while vLLM waiting remains
bounded or zero, verifying the gateway preserves engine responsiveness and prevents uncoordinated
head-of-line blocking. Non-zero preemptions confirm engine-level KV evictions.

""",
}

EXPECTED = {
    "E0": "As load rises, throughput should eventually flatten or fall and queueing/tail latency should rise. KV occupancy and scheduler evidence should identify the first limiter; an all-zero goodput curve cannot locate a useful goodput knee.",
    "E1": "The first request may be slower than subsequent requests after readiness, but the gap can be small when startup performs initialization in advance. Startup recovery time is a separate measurement.",
    "E2": "The reused arm should reduce first-turn prefill latency when matching prefixes remain cached. Later turns may already reuse prefixes within the cold arm, so an improvement on every turn is not required.",
    "E3": "Prefix affinity may improve reuse but concentrate a cold burst on one worker. With the headline trace, the overlap gate should fall back to load from turn 2; a uniform latency improvement is not guaranteed.",
    "E4": "Under genuine overload, early shedding should preserve interactive goodput and limit tail latency while avoiding unacceptable batch starvation. Lower raw throughput can be an intentional trade-off.",
    "E5": "A cached destination may avoid repeated prefill relative to recompute. These controls establish locality costs; a transfer crossover cannot be observed until real KV transfer is implemented and measured.",
    "MEM": "Preallocated HBM can stay flat while KV occupancy and queueing vary. Resource pressure should align in time with workload activity; short bursts may be missed by coarse samples.",
    "QUEUE": "Gateway replica queues absorb offered load over the concurrency limit while vLLM waiting remains near zero, proving the gateway queue protects the engine scheduler rather than duplicating it.",
}
for experiment, hypothesis in EXPECTED.items():
    CONTEXT[experiment] += "**Expected behavior (hypothesis):** " + hypothesis + "\n\n**Observed evidence:** charts and tables below use only supplied run artifacts; the session writeup records interpretation. Missing runs are skipped.\n\n"


CELLS = [
    (
        MD,
        "# #123 evidence notebook\n\nThin view over `experiments/analysis`; contains no results. Point `RUNS` "
        "at pulled run directories (see `docs/inference-experiments/inference-run-playbook.md`). Every output states its evidence "
        "scope: **per-request** (requests.jsonl) or **WINDOW-level** (Prometheus; never per request).",
    ),
    (MD, OVERVIEW),
    ("code", SETUP),
    ("code", CHARTS),
    (MD, "## E0 Capacity and offered-load knee\n\n" + CONTEXT["E0"] + "`completed_rps` is successful completions divided by elapsed seconds. "
         "`good_rps` is SLO-qualified requests divided by the same duration: completion alone is insufficient. "
         "The replayer checks TTFT and E2E against the recorded SLOs. Zero goodput means no requests met both limits; "
         "it does not mean no requests completed. An all-zero curve cannot establish a useful goodput knee. "
         "Client transport latency is included; do not read it as GPU-only latency."),
    ("code", E0),
    (MD, "## E1 Cold vs declared-warm worker\n\n" + CONTEXT["E1"] + "Compare the first observed request with subsequent-request p50. This is request latency after readiness, not full worker startup time. Empty summaries cannot prove warmup occurred."),
    ("code", E1),
    (MD, "## E2 Prefix reuse (cold vs reused)\n\n" + CONTEXT["E2"] + "Inspect turn 1 separately: the cold arm can reuse prefixes on its own later turns. Manifest parity gates causal comparisons; unmatched runs remain descriptive. Prometheus hit rates describe a window, never an individual request."),
    ("code", E2),
    (MD, "## E3 Routing: least_loaded vs prefix_then_load\n\n" + CONTEXT["E3"] + "Compare first-turn and later-turn latency, tail latency and worker distribution together. Better reuse can trade off against queueing on one worker. Check manifest parity and policy verification before declaring a winner; a single pair does not establish a repeatable advantage."),
    ("code", E3),
    (MD, "## E4 Admission on vs off\n\n" + CONTEXT["E4"]),
    ("code", E4),
    (MD, "## E5 Recompute control\n\n" + CONTEXT["E5"]),
    ("code", E5),
    (MD, "## Memory proof\n\n" + CONTEXT["MEM"]),
    ("code", MEM),
    (MD, "## Gateway queue vs engine waiting proof\n\n" + CONTEXT["QUEUE"]),
    ("code", QUEUE),
]



def build() -> dict:
    cells = []
    for i, (kind, src) in enumerate(CELLS):
        cell = {
            "id": f"cell-{i:02d}",
            "cell_type": kind,
            "metadata": {},
            "source": src.splitlines(True),
        }
        if kind == "code":
            cell.update(execution_count=None, outputs=[])
        cells.append(cell)
    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def main() -> None:
    """Regenerate the clean template and optionally execute/export local evidence."""
    import argparse
    import os

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-json", type=Path, help="JSON file mapping experiment keys to run directories")
    parser.add_argument("--writeup-dir", type=Path, help="Optional directory of e0.md through e3.md talk tracks")
    parser.add_argument("--html", type=Path, help="Execute and export HTML plus an executed sibling .ipynb")
    args = parser.parse_args()
    if (args.runs_json or args.writeup_dir) and not args.html:
        parser.error("--runs-json and --writeup-dir require --html to render evidence")
    out = Path(__file__).with_name("123_evidence.ipynb")
    out.write_text(json.dumps(build(), indent=1) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    if not args.html:
        return

    import nbformat
    from nbclient import NotebookClient
    from nbconvert import HTMLExporter

    overrides = {}
    if args.runs_json:
        runs = json.loads(args.runs_json.read_text(encoding="utf-8"))
        if not isinstance(runs, dict):
            parser.error("runs JSON must be an object mapping experiment keys to directories")
        overrides["EVIDENCE_RUNS_JSON"] = json.dumps(runs)
    if args.writeup_dir:
        overrides["EVIDENCE_WRITEUP_DIR"] = str(args.writeup_dir.resolve())
    previous = {key: os.environ.get(key) for key in overrides}
    try:
        os.environ.update(overrides)
        nb = nbformat.reads(json.dumps(build()), as_version=4)
        NotebookClient(nb, timeout=180, kernel_name="python3",
                       resources={"metadata": {"path": str(Path(__file__).resolve().parents[1])}}).execute()
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    executed = args.html.with_suffix(".ipynb")
    if executed.resolve() == out.resolve():
        parser.error("HTML output would overwrite the result-free notebook template")
    html, _ = HTMLExporter(exclude_input=True).from_notebook_node(nb)
    args.html.parent.mkdir(parents=True, exist_ok=True)
    args.html.write_text(html, encoding="utf-8")
    nbformat.write(nb, executed)
    images = sum("image/png" in output.get("data", {})
                 for cell in nb.cells for output in cell.get("outputs", []))
    print(f"wrote {args.html} and {executed} ({images} embedded charts)")


if __name__ == "__main__":
    main()
