"""Opt-in bundle retains HAMi slices and uses host TCP storage, never applies itself."""

import json

import pytest
import yaml

from infra.inference.mooncake.render import render


def test_bundle_is_pinned_and_retains_hami():
    docs = render("lab/kv:v1", "inference-lab", "smoke-001", "template-v1", "prefix-v1")
    for worker in docs[:2]:
        spec = worker["spec"]["template"]["spec"]
        assert spec["schedulerName"] == "hami-scheduler"
        c = spec["containers"][0]
        assert c["resources"]["limits"]["nvidia.com/gpumem-percentage"] == "50"
        args = c["args"]
        conf = json.loads(args[args.index("--kv-transfer-config") + 1])
        assert conf["kv_connector_module_path"] == "kv_transfer.connector"
    config = yaml.safe_load(docs[2]["data"]["base.yaml"])
    assert config["local_cpu"] is False
    assert config["extra_config"]["protocol"] == "tcp"
    service = next(document for document in docs if document["kind"] == "Service")
    assert service["spec"]["type"] == "ClusterIP"
    gateway = docs[-1]
    assert gateway["metadata"]["name"] == "inference-gateway"
    gateway_env = {
        item["name"]: item["value"]
        for item in gateway["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert gateway_env["KV_HOP_ENABLED"] == "1"
    assert gateway_env["ALLOW_FORCED_PLACEMENT"] == "1"


def test_unpinned_image_rejected():
    with pytest.raises(ValueError):
        render("lab/kv:latest", "lab", "v1", "v1", "v1")
