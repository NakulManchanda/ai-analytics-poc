import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


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
    assert "preflight-paid-smoke:" in makefile
    assert "Refusing paid smoke without explicit EVAL_CONFIRM=1" in makefile


def test_preflight_script_covers_env_services_and_eval_modes() -> None:
    script = (ROOT / "scripts/preflight/readiness.py").read_text(encoding="utf-8")
    assert 'choices=("env", "services", "eval", "all")' in script
    assert "GLOBAL_BEDROCK_MONTHLY_LIMIT_USD" in script
    assert "tooling/evals/datasets/golden/v1/splits.json" in script
    assert "docker-compose.observability.yml" in script


def test_preflight_services_and_eval_modes_run_locally() -> None:
    for mode in ("services", "eval"):
        subprocess.run(
            ["python3", "scripts/preflight/readiness.py", mode],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
