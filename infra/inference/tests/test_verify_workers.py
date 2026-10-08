"""verify-workers.sh must exit non-zero when the two workers differ or cannot be compared."""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify-workers.sh"
FAKE_KUBECTL = """#!/usr/bin/env bash
case "$*" in
  *custom-columns*) echo "table" ;;
  *"deploy inference-worker-a -o jsonpath"*) printf '%s' "$ARGS_A" ;;
  *"deploy inference-worker-b -o jsonpath"*) printf '%s' "$ARGS_B" ;;
  *"logs deploy/inference-worker-a"*) cat "$LOG_A" ;;
  *"logs deploy/inference-worker-b"*) cat "$LOG_B" ;;
esac
"""
LOG = (
    "Initializing a V1 LLM engine (v0.11.0) with config: dtype=torch.bfloat16, "
    "kv_cache_dtype=auto, tokenizer_revision=abc123\\n"
)


def remote_body() -> str:
    match = re.search(r"<<'REMOTE'\n(.*?)\nREMOTE\n", SCRIPT.read_text(), re.S)
    assert match, "verify-workers.sh must keep its REMOTE heredoc"
    return match.group(1)


def run(tmp_path: Path, *, args_a="--x 1", args_b="--x 1", log_a=LOG, log_b=LOG):
    (tmp_path / "bin").mkdir()
    kubectl = tmp_path / "bin" / "kubectl"
    kubectl.write_text(FAKE_KUBECTL)
    kubectl.chmod(kubectl.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "a.log").write_text(log_a.replace("\\n", "\n"))
    (tmp_path / "b.log").write_text(log_b.replace("\\n", "\n"))
    env = {
        **os.environ,
        "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
        "NS": "inference-lab",
        "ARGS_A": args_a,
        "ARGS_B": args_b,
        "LOG_A": str(tmp_path / "a.log"),
        "LOG_B": str(tmp_path / "b.log"),
    }
    return subprocess.run(
        ["bash", "-s"], input=remote_body(), env=env, text=True, capture_output=True
    )


def test_identical_workers_pass(tmp_path: Path) -> None:
    result = run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "args identical across workers: yes" in result.stdout
    assert "engine settings identical across workers: yes" in result.stdout


def test_different_args_fail(tmp_path: Path) -> None:
    result = run(tmp_path, args_b="--x 2")
    assert result.returncode == 1 and "args identical across workers: NO" in result.stdout


def test_different_engine_settings_fail(tmp_path: Path) -> None:
    result = run(tmp_path, log_b=LOG.replace("kv_cache_dtype=auto", "kv_cache_dtype=fp8"))
    assert result.returncode == 1
    assert "engine settings identical across workers: NO" in result.stdout


@pytest.mark.parametrize("which", ["log_a", "log_b"])
def test_missing_startup_lines_are_unverified_and_fail(tmp_path: Path, which: str) -> None:
    result = run(tmp_path, **{which: "nothing useful\\n"})
    assert result.returncode == 1 and "UNVERIFIED" in result.stdout
