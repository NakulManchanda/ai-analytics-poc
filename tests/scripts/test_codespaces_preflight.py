import importlib.util
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
READINESS_PATH = ROOT / "scripts/preflight/readiness.py"


def _load_readiness_module():
    spec = importlib.util.spec_from_file_location("preflight_readiness", READINESS_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_devcontainer_contract_for_codespaces_readiness() -> None:
    devcontainer = ROOT / ".devcontainer/devcontainer.json"
    assert devcontainer.exists()
    content = json.loads(devcontainer.read_text(encoding="utf-8"))

    assert content["image"] == "mcr.microsoft.com/devcontainers/python:3.12-bookworm"
    assert content["hostRequirements"] == {
        "cpus": 4,
        "memory": "16gb",
        "storage": "32gb",
    }
    assert content["postCreateCommand"] == "bash .devcontainer/post-create.sh"

    features = content["features"]
    assert "ghcr.io/devcontainers/features/node:1" in features
    assert "ghcr.io/devcontainers/features/docker-outside-of-docker:1" in features
    assert "ghcr.io/devcontainers/features/aws-cli:1" in features


def test_preflight_targets_are_exposed_with_explicit_paid_gate() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "preflight-env:" in makefile
    assert "preflight-services:" in makefile
    assert "preflight-eval:" in makefile
    assert "preflight-runtime-local:" in makefile
    assert "preflight-runtime-aws:" in makefile
    assert "preflight-paid-smoke:" in makefile
    assert "Refusing paid smoke without explicit EVAL_CONFIRM=1" in makefile


def test_aws_credential_validation_positive_and_negative() -> None:
    module = _load_readiness_module()

    assert module.validate_aws_credentials_env({}) is None
    assert (
        module.validate_aws_credentials_env(
            {
                "AWS_ACCESS_KEY_ID": "ASIAEXAMPLEKEY",
                "AWS_SECRET_ACCESS_KEY": "temporary-secret",
                "AWS_SESSION_TOKEN": "temporary-token",
            }
        )
        is None
    )

    permanent = module.validate_aws_credentials_env(
        {
            "AWS_ACCESS_KEY_ID": "AKIAEXAMPLEKEY",
            "AWS_SECRET_ACCESS_KEY": "permanent-secret",
            "AWS_SESSION_TOKEN": "session-token",
        }
    )
    assert permanent is not None and "permanent" in permanent.lower()

    incomplete = module.validate_aws_credentials_env(
        {
            "AWS_ACCESS_KEY_ID": "ASIAEXAMPLEKEY",
            "AWS_SECRET_ACCESS_KEY": "temporary-secret",
        }
    )
    assert incomplete is not None and "incomplete" in incomplete.lower()


def test_runtime_local_reuses_existing_smoke_checks() -> None:
    module = _load_readiness_module()
    calls: list[tuple[list[str], str]] = []

    def fake_run(command, *, label, env_overrides=None, cwd=ROOT):
        calls.append((command, label))

    module._run_checked = fake_run
    module.check_runtime_local()

    labels = [label for _, label in calls]
    assert "app health smoke" in labels
    assert "mcp/duckdb smoke" in labels
    assert "status endpoint (redis/sse contract)" in labels


def test_runtime_local_reports_failures() -> None:
    env = os.environ.copy()
    env["AI_APP_URL"] = "http://127.0.0.1:9"
    result = subprocess.run(
        ["python3", "scripts/preflight/readiness.py", "runtime-local"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "app health smoke failed" in result.stderr


def test_preflight_services_and_eval_modes_run_locally() -> None:
    for mode in ("services", "eval"):
        subprocess.run(
            ["python3", "scripts/preflight/readiness.py", mode],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
