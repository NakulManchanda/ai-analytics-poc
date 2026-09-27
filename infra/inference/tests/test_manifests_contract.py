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


def test_services_are_clusterip_only_and_pods_cannot_bind_host_network_ports() -> None:
    documents = _yaml_documents()
    assert documents, "Kubernetes manifests must be parseable YAML documents"

    services = [doc for _path, doc in documents if doc.get("kind") == "Service"]
    worker_services = {
        doc.get("metadata", {}).get("name"): doc
        for doc in services
        if doc.get("metadata", {}).get("name") in {"inference-worker-a", "inference-worker-b"}
    }
    assert set(worker_services) == {"inference-worker-a", "inference-worker-b"}
    for name, service in worker_services.items():
        assert service.get("spec", {}).get("type", "ClusterIP") == "ClusterIP", name

    for path, document in documents:
        for key, value in _walk(document):
            assert key != "hostPort", f"{path.relative_to(ROOT)} must not use hostPort"
            assert not (key == "hostNetwork" and value is True), (
                f"{path.relative_to(ROOT)} must not use hostNetwork"
            )
        if document.get("kind") == "Service":
            assert document.get("spec", {}).get("type", "ClusterIP") != "NodePort", (
                f"{path.relative_to(ROOT)} must not expose a NodePort"
            )


def test_images_and_versions_are_pinned_without_latest() -> None:
    for path, document in _yaml_documents():
        for key, value in _walk(document):
            if key == "image" and isinstance(value, str):
                assert not value.endswith(":latest"), (
                    f"{path.relative_to(ROOT)} uses unpinned image tag ':latest': {value}"
                )


def test_hami_worker_slices_spec_is_present() -> None:
    slice_path = K8S / "hami" / "worker-slices.yaml"
    assert slice_path.is_file(), "worker-slices.yaml must exist under k8s/hami"
    content = slice_path.read_text(encoding="utf-8")
    assert "device_split_count" in content
    assert "worker_a_gpumem" in content
    assert "worker_b_gpumem" in content


def test_issue_120_has_no_gateway_or_future_scope_resources() -> None:
    forbidden_path_parts = {"gateway", "keda", "mooncake", "lmcache", "open-webui", "ui"}
    bundle_paths = [path.relative_to(BUNDLE).parts for path in BUNDLE.rglob("*")]
    offending_paths = [parts for parts in bundle_paths if forbidden_path_parts & set(parts)]
    assert not offending_paths, f"Future-scope paths are not permitted: {offending_paths}"

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
