"""Contract tests for issue #120 lifecycle and configuration scripts."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BUNDLE = ROOT / "infra" / "inference"
SCRIPTS = BUNDLE / "scripts"

LIFECYCLE_SCRIPTS = (
    "sync.sh",
    "config.sh",
    "secret.sh",
    "bootstrap.sh",
    "deploy.sh",
    "tunnel.sh",
    "smoke.sh",
    "pull-evidence.sh",
    "teardown.sh",
)

MAKE_TARGETS = (
    "inference-validate",
    "inference-sync",
    "inference-config",
    "inference-secret",
    "inference-bootstrap",
    "inference-deploy",
    "inference-up",
    "inference-tunnel",
    "inference-connect",
    "inference-smoke",
    "inference-warmup",
    "inference-capacity",
    "inference-run",
    "inference-pull-evidence",
    "inference-teardown",
)

REQUIRED_ENV_NAMES = {
    "LAMBDA_SSH_HOST",
    "LAMBDA_SSH_USER",
    "LAMBDA_SSH_KEY_PATH",
    "INFERENCE_NAMESPACE",
    "INFERENCE_MODEL",
    "INFERENCE_MODEL_REVISION",
    "INFERENCE_VLLM_IMAGE",
    "INFERENCE_K3S_VERSION",
    "INFERENCE_HELM_VERSION",
    "INFERENCE_HAMI_VERSION",
}

SAFE_REMOTE_CONFIG_NAMES = {
    "INFERENCE_NAMESPACE",
    "INFERENCE_MODEL",
    "INFERENCE_MODEL_REVISION",
    "INFERENCE_VLLM_IMAGE",
    "INFERENCE_K3S_VERSION",
    "INFERENCE_HELM_VERSION",
    "INFERENCE_HAMI_VERSION",
}


def test_env_template_declares_required_names_without_secret_values() -> None:
    env_example = BUNDLE / ".env.example"
    assert env_example.is_file(), "A tracked, value-safe .env.example is required"
    content = env_example.read_text(encoding="utf-8")
    declared = {
        line.split("=", 1)[0]
        for line in content.splitlines()
        if line and not line.lstrip().startswith("#") and "=" in line
    }
    assert REQUIRED_ENV_NAMES <= declared
    assert "BEGIN " not in content
    assert "PRIVATE KEY" not in content


def test_root_makefile_exposes_safe_inference_entry_points() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    missing = [target for target in MAKE_TARGETS if f"{target}:" not in makefile]
    assert not missing, f"Missing root Make targets: {missing}"

    for target in MAKE_TARGETS:
        result = subprocess.run(
            ["make", "--dry-run", target],
            cwd=ROOT,
            text=True,
            capture_output=True,
        )
        assert result.returncode == 0, (
            f"{target} must be locally inspectable with make --dry-run:\n{result.stderr}"
        )

    assert "inference-connect: inference-sync" in makefile
    assert "inference-tunnel" in makefile
    expected_up = (
        "inference-up: inference-sync inference-config inference-bootstrap inference-deploy"
    )
    assert expected_up in makefile
    expected_run = (
        "inference-run: inference-smoke inference-warmup inference-capacity inference-pull-evidence"
    )
    assert expected_run in makefile


def test_lifecycle_scripts_offer_help_and_fail_closed_without_connection_config() -> None:
    missing_env = os.environ.copy()
    for name in REQUIRED_ENV_NAMES:
        missing_env.pop(name, None)
    missing_env["INFERENCE_ENV_FILE"] = str(BUNDLE / "does-not-exist.env")

    for script_name in LIFECYCLE_SCRIPTS:
        script = SCRIPTS / script_name
        assert script.is_file(), f"Missing {script_name}"
        help_result = subprocess.run(
            ["bash", str(script), "--help"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            env=missing_env,
        )
        assert help_result.returncode == 0, f"{script_name} --help failed: {help_result.stderr}"

    sync_result = subprocess.run(
        ["bash", str(SCRIPTS / "sync.sh")],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=missing_env,
    )
    assert sync_result.returncode != 0
    assert "LAMBDA_SSH_HOST" in (sync_result.stdout + sync_result.stderr)


def test_sync_plan_is_allowlisted_and_does_not_print_connection_secret_values() -> None:
    sync_script = SCRIPTS / "sync.sh"
    assert sync_script.is_file(), "sync must be an explicit, reviewable bundle operation"
    plan_env = os.environ.copy()
    plan_env.update(
        {
            "LAMBDA_SSH_HOST": "inference-test-host.invalid",
            "LAMBDA_SSH_USER": "inference-test-user",
            "LAMBDA_SSH_KEY_PATH": "/private/inference-test-private-key.pem",
            "HF_TOKEN": "inference-test-token-must-not-print",
        }
    )
    result = subprocess.run(
        ["bash", str(sync_script), "--plan"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=plan_env,
    )
    assert result.returncode == 0, result.stderr
    plan = result.stdout + result.stderr

    assert re.search(r"source\s*[:=].*infra/inference", plan, re.I)
    for excluded in (
        ".env",
        "credentials",
        "metrics",
        "services/app",
        "services/mcp",
        "web",
        ".pem",
        ".key",
    ):
        assert excluded in plan, f"sync plan must explicitly exclude {excluded}"
    assert "inference-test-private-key.pem" not in plan
    assert "inference-test-token-must-not-print" not in plan


def test_config_plan_exposes_only_allowlisted_names_not_raw_env_values() -> None:
    config_script = SCRIPTS / "config.sh"
    assert config_script.is_file(), "config must be separate from bundle sync"
    config_env = os.environ.copy()
    config_env.update(
        {
            "LAMBDA_SSH_HOST": "inference-test-host.invalid",
            "LAMBDA_SSH_KEY_PATH": "/private/inference-test-private-key.pem",
            "HF_TOKEN": "inference-test-token-must-not-print",
        }
    )
    result = subprocess.run(
        ["bash", str(config_script), "--print-keys"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=config_env,
    )
    assert result.returncode == 0, result.stderr
    printed = result.stdout + result.stderr
    for name in SAFE_REMOTE_CONFIG_NAMES:
        assert name in printed
    assert ".env" not in printed
    assert "LAMBDA_SSH_KEY_PATH" not in printed
    assert "HF_TOKEN" not in printed
    assert "inference-test-private-key.pem" not in printed
    assert "inference-test-token-must-not-print" not in printed
