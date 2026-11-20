"""#179: regression tests for Make eval-run and eval-temporal-run split defaults.

Proves that after `make eval-start LABEL=<name> EVAL_SPLIT=holdout`, running
`make eval-run` or `make eval-temporal-run` WITHOUT repeating `EVAL_SPLIT` uses the
saved manifest split (omits `--all`), while an unsplit run still passes `--all`.
Zero paid Bedrock calls (dry-run via `make -n`).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from evals.cli import main

REPO_ROOT = Path(__file__).resolve().parents[3]


def _make_dry_run(target: str, eval_root: Path) -> str:
    res = subprocess.run(
        [
            "make",
            "-n",
            target,
            f"EVAL_ROOT={eval_root}",
            "EVAL_CONFIRM=1",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return res.stdout


def test_make_eval_run_and_temporal_run_use_saved_manifest_split(
    tmp_path: Path,
) -> None:
    # 1. Start a split-pinned run (same as `make eval-start LABEL=ho-1 EVAL_SPLIT=holdout`).
    assert (
        main(
            [
                "run-start",
                "--root",
                str(tmp_path),
                "--label",
                "ho-1",
                "--split",
                "holdout",
            ]
        )
        == 0
    )

    # 2. Both `make eval-run` and `make eval-temporal-run` WITHOUT EVAL_SPLIT must omit `--all`.
    live_cmd = _make_dry_run("eval-run", tmp_path)
    assert "run-live" in live_cmd
    assert "--all" not in live_cmd, f"split run must not pass --all: {live_cmd}"

    temporal_cmd = _make_dry_run("eval-temporal-run", tmp_path)
    assert "run-temporal" in temporal_cmd
    assert "--all" not in temporal_cmd, f"split run must not pass --all: {temporal_cmd}"

    # 3. An unsplit run (no --split / no --cases) must still default to `--all`.
    assert main(["run-start", "--root", str(tmp_path), "--label", "full-1"]) == 0
    assert "--all" in _make_dry_run("eval-run", tmp_path)
    assert "--all" in _make_dry_run("eval-temporal-run", tmp_path)
