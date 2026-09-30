"""The evidence notebook is valid, result-free, regenerated from its script, and runs offline."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NB = ROOT / "experiments" / "123_evidence.ipynb"
FIX = Path(__file__).parent / "fixtures"


def _code(nb: dict) -> str:
    return "\n".join(
        "".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"
    )


def test_notebook_is_generated_valid_and_has_no_outputs_or_results() -> None:
    nb = json.loads(NB.read_text())
    assert nb["nbformat"] == 4 and len({c["id"] for c in nb["cells"]}) == len(
        nb["cells"]
    )
    assert all(c["outputs"] == [] for c in nb["cells"] if c["cell_type"] == "code")
    headings = " ".join(
        "".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "markdown"
    )
    for exp in ("E0", "E1", "E2", "E3", "E4", "E5", "Memory proof"):
        assert exp in headings
    try:
        import nbformat
    except ImportError:  # only in the experiments uv project
        pass
    else:
        nbformat.validate(nbformat.read(NB, as_version=4))
    # committed file must equal the generator output (diff-friendly, no hand edits)
    sys.path.insert(0, str(ROOT / "experiments"))
    import build_notebook

    assert json.loads(NB.read_text()) == build_notebook.build()


def _run_cells(env_runs: dict | None) -> str:
    env = {**os.environ, "EVIDENCE_RUNS_JSON": json.dumps(env_runs or {})}
    code = _code(json.loads(NB.read_text()))
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env, text=True, capture_output=True
    )
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_notebook_degrades_gracefully_without_runs() -> None:
    out = _run_cells(None)
    assert out.count("run directory not provided") == 8


def test_notebook_executes_offline_against_fixtures() -> None:
    runs = {
        "E0_SWEEP": FIX / "sweep_run",
        "E1_WARMUP_COLD": FIX / "warmup_cold",
        "E1_WARMUP_WARM": FIX / "warmup_warm",
        "E2_COLD": FIX / "e3_least_loaded",
        "E2_REUSED": FIX / "e3_prefix_then_load",
        "E3_LEAST_LOADED": FIX / "e3_least_loaded",
        "E3_PREFIX_THEN_LOAD": FIX / "e3_prefix_then_load",
        "E4_ADMISSION_OFF": FIX / "e4_admission_off",
        "E4_ADMISSION_ON": FIX / "e4_admission_on",
        "E5_RUN": FIX / "e3_prefix_then_load",
        "MEMORY_RANGE": FIX / "prometheus_range",
    }
    out = _run_cells({k: str(v) for k, v in runs.items()})
    assert "run directory not provided" in out  # only E0_CAPACITY is absent
    assert out.count("run directory not provided") == 1
    for needle in (
        "E3 least_loaded",
        "E4 admission off",
        "WINDOW-level",
        "knee_offered_concurrency: 4",
        "flat_at_max",
    ):
        assert needle in out
