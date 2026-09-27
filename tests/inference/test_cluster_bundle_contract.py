"""Executable boundary contracts for issue #120's Lambda/k3s lab.

These tests deliberately describe the smallest safe, self-contained cluster bundle.
They must stay independent of a live Lambda host: rendering and fail-closed checks are
local; real GPU acceptance belongs to the operator run.
"""

from __future__ import annotations

import copy
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "infra" / "inference"
SCRIPTS = BUNDLE / "scripts"
K8S = BUNDLE / "k8s"

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


def _yaml_documents() -> list[tuple[Path, dict[str, Any]]]:
    """Load every rendered/static Kubernetes document in the bundle."""
    documents: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(K8S.rglob("*.y*ml")):
        for document in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if isinstance(document, dict) and document:
                documents.append((path, document))
    return documents


def _find_kind(name: str) -> list[tuple[Path, dict[str, Any]]]:
    return [(path, doc) for path, doc in _yaml_documents() if doc.get("kind") == name]


def _normalise_worker_identity(value: Any) -> Any:
    """Remove the only permitted A/B difference: worker identity."""
    if isinstance(value, dict):
        return {key: _normalise_worker_identity(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalise_worker_identity(item) for item in value]
    if isinstance(value, str):
        return (
            value.replace("worker-a", "worker-IDENTITY")
            .replace("worker-b", "worker-IDENTITY")
            .replace("worker_a", "worker_IDENTITY")
            .replace("worker_b", "worker_IDENTITY")
            .replace("workerA", "workerIDENTITY")
            .replace("workerB", "workerIDENTITY")
        )
    return value


def _walk(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key, item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def test_bundle_is_a_self_contained_issue_120_lab() -> None:
    assert BUNDLE.is_dir(), "#120 must add the transferable infra/inference bundle"

    required_paths = (
        ".env.example",
        "README.md",
        "scripts",
        "k8s",
        "k8s/hami",
        "k8s/workers",
        "k8s/services",
        "observability/prometheus",
        "observability/grafana",
        "observability/dcgm",
        "experiments",
    )
    missing = [path for path in required_paths if not (BUNDLE / path).exists()]
    assert not missing, f"Missing #120 bundle paths: {missing}"

    missing_scripts = [
        name for name in LIFECYCLE_SCRIPTS if not (SCRIPTS / name).is_file()
    ]
    assert not missing_scripts, f"Missing lifecycle scripts: {missing_scripts}"


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
        assert (
            result.returncode == 0
        ), f"{target} must be locally inspectable with make --dry-run:\n{result.stderr}"

    assert "inference-connect: inference-sync" in makefile
    expected_up = (
        "inference-up: inference-sync inference-bootstrap "
        "inference-config inference-deploy"
    )
    assert expected_up in makefile
    expected_run = (
        "inference-run: inference-smoke inference-warmup "
        "inference-capacity inference-pull-evidence"
    )
    assert expected_run in makefile


def test_lifecycle_scripts_offer_help_and_fail_closed_without_connection_config() -> (
    None
):
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
        assert (
            help_result.returncode == 0
        ), f"{script_name} --help failed: {help_result.stderr}"

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
    assert (
        sync_script.is_file()
    ), "sync must be an explicit, reviewable bundle operation"
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


def test_worker_a_and_b_have_semantically_identical_serving_configuration() -> None:
    deployment_docs = _find_kind("Deployment")
    workers: dict[str, dict[str, Any]] = {}
    for _path, document in deployment_docs:
        name = document.get("metadata", {}).get("name", "")
        if name in {"inference-worker-a", "inference-worker-b"}:
            workers[name] = document

    assert set(workers) == {"inference-worker-a", "inference-worker-b"}
    worker_a = _normalise_worker_identity(copy.deepcopy(workers["inference-worker-a"]))
    worker_b = _normalise_worker_identity(copy.deepcopy(workers["inference-worker-b"]))
    assert worker_a == worker_b, "workers may differ only by identity/service name"


def test_services_are_clusterip_only_and_pods_cannot_bind_host_network_ports() -> None:
    documents = _yaml_documents()
    assert documents, "Kubernetes manifests must be parseable YAML documents"

    services = [doc for _path, doc in documents if doc.get("kind") == "Service"]
    worker_services = {
        doc.get("metadata", {}).get("name"): doc
        for doc in services
        if doc.get("metadata", {}).get("name")
        in {"inference-worker-a", "inference-worker-b"}
    }
    assert set(worker_services) == {"inference-worker-a", "inference-worker-b"}
    for name, service in worker_services.items():
        assert service.get("spec", {}).get("type", "ClusterIP") == "ClusterIP", name

    for path, document in documents:
        for key, value in _walk(document):
            assert key != "hostPort", f"{path.relative_to(ROOT)} must not use hostPort"
            assert not (
                key == "hostNetwork" and value is True
            ), f"{path.relative_to(ROOT)} must not use hostNetwork"
        if document.get("kind") == "Service":
            assert (
                document.get("spec", {}).get("type", "ClusterIP") != "NodePort"
            ), f"{path.relative_to(ROOT)} must not expose a NodePort"


def test_issue_120_has_no_gateway_or_future_scope_resources() -> None:
    assert BUNDLE.is_dir(), "#120 must add the transferable infra/inference bundle"
    forbidden_path_parts = {
        "gateway",
        "keda",
        "mooncake",
        "lmcache",
        "open-webui",
        "ui",
    }
    bundle_paths = [path.relative_to(BUNDLE).parts for path in BUNDLE.rglob("*")]
    offending_paths = [
        parts for parts in bundle_paths if forbidden_path_parts & set(parts)
    ]
    assert (
        not offending_paths
    ), f"Future-scope paths are not permitted: {offending_paths}"

    forbidden_resource_words = ("gateway", "keda", "mooncake", "lmcache", "openwebui")
    offenders: list[str] = []
    for path, document in _yaml_documents():
        metadata = document.get("metadata", {})
        identity = " ".join(
            str(part).lower()
            for part in (document.get("kind", ""), metadata.get("name", ""))
        )
        if any(word in identity for word in forbidden_resource_words):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"#120 must not define future-scope resources: {offenders}"
