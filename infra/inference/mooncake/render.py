"""Render an opt-in Mooncake lab bundle; this command never applies it.

Uses CPU staging and TCP for two HAMi workers. Exact model/tokenizer/prefix
contracts are mandatory; a fresh compatibility namespace isolates old caches.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def render(
    image: str,
    namespace: str,
    cache_namespace: str,
    template_version: str,
    prefix_contract_version: str,
):
    if (
        not image
        or image.endswith(":latest")
        or not all((namespace, cache_namespace, template_version, prefix_contract_version))
    ):
        raise ValueError("pinned image and explicit namespace/template versions required")
    docs = []
    for worker in ("a", "b"):
        doc = yaml.safe_load((ROOT / f"k8s/workers/worker-{worker}.yaml").read_text())
        doc["metadata"]["namespace"] = namespace
        container = doc["spec"]["template"]["spec"]["containers"][0]
        container["image"] = image
        args = container["args"]
        revision = args[args.index("--revision") + 1]
        args.extend(
            [
                "--tokenizer-revision",
                revision,
                "--kv-transfer-config",
                json.dumps(
                    {
                        "kv_connector": "MooncakeKVConnector",
                        "kv_role": "kv_both",
                        "kv_connector_module_path": "kv_transfer.connector",
                    }
                ),
            ]
        )
        container["env"].extend(
            [
                {"name": "KV_HOP_ENABLED", "value": "1"},
                {"name": "KV_WORKER_ID", "value": f"worker_{worker}"},
                {"name": "KV_CACHE_NAMESPACE", "value": cache_namespace},
                {"name": "KV_TEMPLATE_VERSION", "value": template_version},
                {"name": "KV_PREFIX_CONTRACT_VERSION", "value": prefix_contract_version},
                {"name": "KV_TOKENIZER_REVISION", "value": revision},
                {"name": "POD_IP", "valueFrom": {"fieldRef": {"fieldPath": "status.podIP"}}},
            ]
        )
        config = {
            "chunk_size": 256,
            "remote_url": "mooncakestore://mooncake:50051/",
            "remote_serde": "naive",
            "local_cpu": False,
            "max_local_cpu_size": 1,
            "use_layerwise": False,
            "enable_async_loading": False,
            "extra_config": {
                "local_hostname": "REPLACED_AT_STARTUP",
                "metadata_server": "http://mooncake:8080/metadata",
                "protocol": "tcp",
                "master_server_address": "mooncake:50051",
                "global_segment_size": 1073741824,
                "local_buffer_size": 268435456,
                "transfer_timeout": 1,
            },
        }
        # LMCache v0.3.9 reads local_hostname literally: render POD_IP before engine launch.
        container["command"] = ["/bin/bash", "-ec"]
        container["args"] = [
            'python -c \'import os,yaml; p=yaml.safe_load(open("/etc/lmcache/base.yaml")); '
            'p["extra_config"]["local_hostname"]=os.environ["POD_IP"]; '
            'yaml.safe_dump(p,open("/tmp/lmcache.yaml","w"))\'; '
            "export LMCACHE_CONFIG_FILE=/tmp/lmcache.yaml; exec python -m "
            'vllm.entrypoints.openai.api_server "$@"',
            "--",
            *args,
        ]
        container["volumeMounts"] = [{"name": "lmcache", "mountPath": "/etc/lmcache"}]
        doc["spec"]["template"]["spec"]["volumes"] = [
            {"name": "lmcache", "configMap": {"name": "lmcache-hop"}}
        ]
        docs.append(doc)
    docs.append(
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "lmcache-hop", "namespace": namespace},
            "data": {"base.yaml": yaml.safe_dump(config)},
        }
    )
    docs.append(
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "mooncake", "namespace": namespace},
            "spec": {
                "replicas": 1,
                "strategy": {"type": "Recreate"},
                "selector": {"matchLabels": {"app": "mooncake"}},
                "template": {
                    "metadata": {"labels": {"app": "mooncake"}},
                    "spec": {
                        "containers": [
                            {
                                "name": "master",
                                "image": image,
                                "command": ["mooncake_master"],
                                "ports": [{"containerPort": 50051}],
                                "resources": {"limits": {"memory": "1Gi", "cpu": "1"}},
                            },
                            {
                                "name": "metadata",
                                "image": image,
                                "command": ["mooncake_http_metadata_server"],
                                "ports": [{"containerPort": 8080}],
                                "resources": {"limits": {"memory": "256Mi", "cpu": "1"}},
                            },
                        ]
                    },
                },
            },
        }
    )
    docs.append(
        {
            "apiVersion": "v1",
            "kind": "Service",
            "metadata": {"name": "mooncake", "namespace": namespace},
            "spec": {
                "type": "ClusterIP",
                "selector": {"app": "mooncake"},
                "ports": [{"name": "master", "port": 50051}, {"name": "metadata", "port": 8080}],
            },
        }
    )
    gateway = yaml.safe_load((ROOT / "k8s/gateway/gateway.yaml").read_text())
    gateway["metadata"]["namespace"] = namespace
    gateway_env = gateway["spec"]["template"]["spec"]["containers"][0]["env"]
    by_name = {entry["name"]: entry for entry in gateway_env}
    for name in ("ALLOW_EXPERIMENT_CONTROLS", "ALLOW_FORCED_PLACEMENT", "KV_HOP_ENABLED"):
        if name in by_name:
            by_name[name]["value"] = "1"
        else:
            gateway_env.append({"name": name, "value": "1"})
    docs.append(gateway)
    return docs


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--image", required=True)
    p.add_argument("--namespace", default="inference-lab")
    p.add_argument("--cache-namespace", required=True)
    p.add_argument("--template-version", required=True)
    p.add_argument("--prefix-contract-version", required=True)
    args = p.parse_args()
    print(
        yaml.safe_dump_all(
            render(
                args.image,
                args.namespace,
                args.cache_namespace,
                args.template_version,
                args.prefix_contract_version,
            ),
            sort_keys=False,
        )
    )


if __name__ == "__main__":
    main()
