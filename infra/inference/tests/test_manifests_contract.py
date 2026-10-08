"""Contract tests for issue #120 Kubernetes and observability manifests."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
BUNDLE = ROOT / "infra" / "inference"
K8S = BUNDLE / "k8s"
OBSERVABILITY = BUNDLE / "observability"


def _yaml_documents() -> list[tuple[Path, dict[str, Any]]]:
    """Load every static Kubernetes document in k8s and observability."""
    documents: list[tuple[Path, dict[str, Any]]] = []
    for base in (K8S, OBSERVABILITY):
        for path in sorted(base.rglob("*.y*ml")):
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


def test_workers_enable_vllm_tool_calling_with_hermes_parser() -> None:
    """#115 slice B: the gateway returned 400 on tool calls because the workers were
    never started with tool-calling enabled. Both workers must carry identical flags."""
    deployment_docs = _find_kind("Deployment")
    workers: dict[str, dict[str, Any]] = {}
    for _path, document in deployment_docs:
        name = document.get("metadata", {}).get("name", "")
        if name in {"inference-worker-a", "inference-worker-b"}:
            workers[name] = document

    assert set(workers) == {"inference-worker-a", "inference-worker-b"}
    for name, document in workers.items():
        containers = document.get("spec", {}).get("template", {}).get("spec", {}).get(
            "containers", []
        )
        vllm_containers = [c for c in containers if c.get("name") == "vllm"]
        assert vllm_containers, f"{name} must define a vllm container"
        args = vllm_containers[0].get("args", [])
        assert "--enable-auto-tool-choice" in args, name
        parser_index = args.index("--tool-call-parser")
        assert args[parser_index + 1] == "hermes", name


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


def test_images_and_versions_are_pinned_without_latest() -> None:
    for path, document in _yaml_documents():
        for key, value in _walk(document):
            if key == "image" and isinstance(value, str):
                assert not value.endswith(
                    ":latest"
                ), f"{path.relative_to(ROOT)} uses unpinned image tag ':latest': {value}"


def test_hami_worker_slices_spec_is_present() -> None:
    slice_path = K8S / "hami" / "worker-slices.yaml"
    assert slice_path.is_file(), "worker-slices.yaml must exist under k8s/hami"
    content = slice_path.read_text(encoding="utf-8")
    assert "device_split_count" in content
    assert "worker_a_gpumem" in content
    assert "worker_b_gpumem" in content


def test_future_scope_resources_are_not_present() -> None:
    forbidden_path_parts = {
        "keda",
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

    forbidden_resource_words = ("keda", "mooncake", "lmcache", "openwebui")
    offenders: list[str] = []
    for path, document in _yaml_documents():
        metadata = document.get("metadata", {})
        identity = " ".join(
            str(part).lower()
            for part in (document.get("kind", ""), metadata.get("name", ""))
        )
        if any(word in identity for word in forbidden_resource_words):
            offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"Must not define future-scope resources: {offenders}"


def test_gateway_manifests_contract() -> None:
    gateway_deployment = K8S / "gateway" / "gateway.yaml"
    assert gateway_deployment.is_file(), "gateway.yaml must exist under k8s/gateway"

    gateway_service = K8S / "services" / "gateway.yaml"
    assert gateway_service.is_file(), "gateway.yaml must exist under k8s/services"

    svc_docs = yaml.safe_load(gateway_service.read_text(encoding="utf-8"))
    assert svc_docs.get("spec", {}).get("type") == "ClusterIP"
    ports = svc_docs.get("spec", {}).get("ports", [])
    assert any(p.get("port") == 8080 for p in ports)



def _container_env(doc: dict[str, Any]) -> dict[str, str]:
    container = doc["spec"]["template"]["spec"]["containers"][0]
    return {item["name"]: item.get("value", "") for item in container.get("env", [])}


def _worker_arg(doc: dict[str, Any], flag: str) -> str:
    args = doc["spec"]["template"]["spec"]["containers"][0]["args"]
    return str(args[args.index(flag) + 1])


def test_gateway_limits_match_worker_engine_flags() -> None:
    """Admission, queue and guard limits come from the engine flags, not from code defaults."""
    deployments = {doc["metadata"]["name"]: doc for _p, doc in _find_kind("Deployment")}
    gateway_env = _container_env(deployments["inference-gateway"])
    for worker in ("inference-worker-a", "inference-worker-b"):
        doc = deployments[worker]
        max_seqs = _worker_arg(doc, "--max-num-seqs")
        max_len = _worker_arg(doc, "--max-model-len")
        assert gateway_env["MAX_DECODE_SLOTS"] == max_seqs, worker
        assert gateway_env["WORKER_MAX_INFLIGHT"] == max_seqs, worker
        assert gateway_env["MAX_MODEL_LEN"] == max_len, worker
        assert int(gateway_env["MAX_PROMPT_TOKENS"]) <= int(max_len), worker


def test_gateway_sets_every_admission_threshold_explicitly() -> None:
    deployments = {doc["metadata"]["name"]: doc for _p, doc in _find_kind("Deployment")}
    gateway_env = _container_env(deployments["inference-gateway"])
    for name in (
        "KV_FREE_MIN",
        "PREFILL_TOKENS_PER_S",
        "QUEUE_WAIT_PER_WAITING_S",
        "WARM_GATE",
        "WARM_MIN_SCRAPES",
        "WARM_PROBE_MODEL",
        "WARM_RAMP_STEPS",
        "WARM_RAMP_STEP_S",
    ):
        assert gateway_env.get(name), f"gateway manifest must set {name}"
