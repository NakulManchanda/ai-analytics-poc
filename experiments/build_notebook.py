"""Generate experiments/123_evidence.ipynb (diff-friendly: no outputs, stable ids, no results).

Run: uv run --project experiments python experiments/build_notebook.py
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
                                  load_run, memory_proof, sweep_curve, ttft_by_turn)
from experiments.analysis.display import show

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
}
RUNS.update(json.loads(os.environ.get("EVIDENCE_RUNS_JSON", "{}")))


def need(*keys):
    """Return the Paths for keys, or print why the experiment cell is skipped."""
    missing = [k for k in keys if not RUNS.get(k)]
    if missing:
        print(f"run directory not provided: set RUNS{missing}; skipping this experiment")
        return None
    return [Path(RUNS[k]) for k in keys]


def plot_sweep(curve):
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return print("matplotlib not installed: table only")
    rows = curve["rows"]
    x = [r["offered_concurrency"] for r in rows]
    for key in ("completed_rps", "good_rps"):
        plt.plot(x, [r[key] for r in rows], marker="o", label=key)
    plt.xlabel("offered concurrency"); plt.ylabel("requests/s"); plt.legend(); plt.show()
'''

E0 = """\
if (p := need("E0_SWEEP")):
    run = load_run(p[0])
    curve = sweep_curve(run)
    show("E0 offered load vs throughput vs goodput (knee)", curve)
    plot_sweep(curve)
if (p := need("E0_CAPACITY")):
    print("E0 capacity runner (synthetic, preliminary):", json.loads((p[0] / "capacity_summary.json").read_text()))
"""

E1 = """\
if (p := need("E1_WARMUP_COLD", "E1_WARMUP_WARM")):
    cold, warm = (json.loads((d / "warmup_summary.json").read_text()) for d in p)
    for worker in sorted(set(cold) | set(warm)):
        print(worker, "| cold run:", cold.get(worker), "| declared-warm run:", warm.get(worker))
"""

E2 = """\
if (p := need("E2_COLD", "E2_REUSED")):
    show("E2 prefix reuse (cold vs reused)", e2_prefix_reuse(load_run(p[0]), load_run(p[1])))
"""

E3 = """\
if (p := need("E3_LEAST_LOADED", "E3_PREFIX_THEN_LOAD")):
    show("E3 least_loaded (a) vs prefix_then_load (b)", e3_compare(load_run(p[0]), load_run(p[1])))
"""

E4 = """\
if (p := need("E4_ADMISSION_OFF", "E4_ADMISSION_ON")):
    show("E4 admission off (a) vs on (b)", e4_compare(load_run(p[0]), load_run(p[1])))
"""

E5 = """\
if (p := need("E5_RUN")):
    run = load_run(p[0])
    show("E5 TTFT by turn", ttft_by_turn(run))
    show("E5 latency by worker", breakdown(run, "worker"))
"""

MEM = """\
if (p := need("MEMORY_RANGE")):
    show("Memory proof (HBM, KV, scheduler, rate, preemptions, prefix hits)", {
        k: v for k, v in memory_proof(p[0]).items() if k not in ("series", "timestamps")})
"""

CELLS = [
    (
        MD,
        "# #123 evidence notebook\n\nThin view over `experiments/analysis`; contains no results. Point `RUNS` "
        "at pulled run directories (see `docs/inference-run-playbook.md`). Every output states its evidence "
        "scope: **per-request** (requests.jsonl) or **WINDOW-level** (Prometheus; never per request).",
    ),
    ("code", SETUP),
    (MD, "## E0 Capacity and offered-load knee"),
    ("code", E0),
    (MD, "## E1 Cold vs declared-warm worker"),
    ("code", E1),
    (MD, "## E2 Prefix reuse (cold vs reused)"),
    ("code", E2),
    (MD, "## E3 Routing: least_loaded vs prefix_then_load"),
    ("code", E3),
    (MD, "## E4 Admission on vs off"),
    ("code", E4),
    (MD, "## E5 Recompute control"),
    ("code", E5),
    (MD, "## Memory proof"),
    ("code", MEM),
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


if __name__ == "__main__":
    out = Path(__file__).with_name("123_evidence.ipynb")
    out.write_text(json.dumps(build(), indent=1) + "\n", encoding="utf-8")
    print(f"wrote {out}")
