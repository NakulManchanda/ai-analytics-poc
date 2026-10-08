"""Run and retain the four controlled KV reuse cases against a live lab.

Run this on the k3s host after deploying the opt-in Mooncake bundle.  The
script never applies Kubernetes resources.  It fails closed when worker logs,
vLLM counters, or response evidence cannot prove a case.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.benchmarks.e5_locality import SIZES as E5_PREFIX_SIZES

try:
    from infra.inference.kv_transfer.evidence import validate_run
    from infra.inference.kv_transfer.identity import CompatibilitySpec
except ModuleNotFoundError as exc:
    # ``make inference-sync`` copies only infra/inference to the GPU host. Keep
    # the proof runner executable there as ``python mooncake/smoke.py`` while
    # preserving normal package imports in the repository and tests.
    if exc.name != "infra":
        raise
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from kv_transfer.evidence import validate_run
    from kv_transfer.identity import CompatibilitySpec

CASES = (
    "same_worker_local_reuse",
    "cross_worker_recompute_transfer_disabled",
    "independently_warmed_destination_local_hit",
    "real_mooncake_transfer_consumed",
)
PREFIX_HITS = re.compile(
    r"^vllm:(?:gpu_)?prefix_cache_hits(?:_total)?(?:\{[^}]*\})?\s+([0-9.eE+-]+)$"
)
TTFT_SUM = re.compile(
    r"^(?:vllm:)?time_to_first_token_seconds_sum(?:\{[^}]*\})?\s+([0-9.eE+-]+)$"
)
TTFT_COUNT = re.compile(
    r"^(?:vllm:)?time_to_first_token_seconds_count(?:\{[^}]*\})?\s+([0-9.eE+-]+)$"
)


def _get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=15) as response:  # noqa: S310 - operator URL
        if response.status != 200:
            raise RuntimeError(f"GET failed with status {response.status}")
        return response.read().decode("utf-8")


def _post(url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - operator URL
        payload = response.read().decode("utf-8")
        if response.status != 200:
            raise RuntimeError(f"request failed with status {response.status}")
        return {
            "status": response.status,
            "headers": dict(response.headers.items()),
            "body": json.loads(payload),
        }


def _kubectl(namespace: str, *args: str) -> str:
    result = subprocess.run(  # noqa: S603 - fixed executable and separated arguments
        ["kubectl", "-n", namespace, *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _prefix_hits(metrics: str) -> float:
    return sum(
        float(match.group(1))
        for line in metrics.splitlines()
        if (match := PREFIX_HITS.match(line.strip()))
    )


def _ttft_stats(metrics: str) -> tuple[float, float]:
    total_sum = sum(
        float(m.group(1))
        for line in metrics.splitlines()
        if (m := TTFT_SUM.match(line.strip()))
    )
    total_count = sum(
        float(m.group(1))
        for line in metrics.splitlines()
        if (m := TTFT_COUNT.match(line.strip()))
    )
    return total_sum, total_count


def _ttft_ms(before: str, after: str) -> float | None:
    sum_before, count_before = _ttft_stats(before)
    sum_after, count_after = _ttft_stats(after)
    count_delta = count_after - count_before
    if count_delta > 0:
        return round(((sum_after - sum_before) / count_delta) * 1000.0, 2)
    return None


def _json_events(log_text: str) -> list[dict[str, Any]]:
    events = []
    for line in log_text.splitlines():
        start = line.find("{")
        if start < 0:
            continue
        try:
            value = json.loads(line[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and str(value.get("event", "")).startswith("kv_hop"):
            events.append(value)
    return events


REPEATED_SENTENCE = " The yellow taxi policy requires careful evidence and bounded analysis."
TOKENS_PER_REPEAT = 11
OVERHEAD_TOKENS = 10


def _nominal_prompt_tokens(prompt: str) -> int:
    """Return nominal estimated prompt tokens based on sentence repetition (~6.5 chars/token)."""
    reps = prompt.count(REPEATED_SENTENCE)
    return OVERHEAD_TOKENS + reps * TOKENS_PER_REPEAT


def _prompt(run_id: str, case: str, prefix_size: str = "4k") -> str:
    target_tokens = E5_PREFIX_SIZES.get(prefix_size, 4096)
    marker = f"KV proof {run_id} {case} {prefix_size}."
    suffix = " Reply with OK."
    reps = max(1, (target_tokens - OVERHEAD_TOKENS) // TOKENS_PER_REPEAT)
    return marker + REPEATED_SENTENCE * reps + suffix


def _request_headers(case: str, request_id: str, worker: str, hop: str) -> dict[str, str]:
    return {
        "x-request-id": request_id,
        "x-conversation-id": f"conv-{request_id}",
        "x-agent-step": "1",
        "x-prefix-id": f"prefix-{case}",
        "x-force-worker": worker,
        "x-kv-hop-mode": hop,
        "x-kv-hop-case": case,
        "x-admission-mode": "off",
        "x-tenant-quota-mode": "off",
        "x-deadline-ms": "30000",
    }


def _target_event(
    case: str,
    request_id: str,
    events: list[dict[str, Any]],
    response: dict[str, Any],
    hit_delta: float,
) -> dict[str, Any]:
    matching = [
        event
        for event in events
        if event.get("request_id") == request_id and event.get("event") == "kv_hop"
    ]
    if not matching:
        raise RuntimeError(f"no final KV event for {case}")
    event = dict(matching[-1])
    if case != "real_mooncake_transfer_consumed":
        if case != "cross_worker_recompute_transfer_disabled":
            if hit_delta <= 0 or event.get("reusable_tokens", 0) <= 0:
                raise RuntimeError(f"no isolated local-prefix hit evidence for {case}")
            event.update(
                source_worker_or_store=event["destination_worker"],
                consumed=response["status"] == 200,
                destination_consumed=response["status"] == 200,
                evidence_scope="isolated_window",
            )
        else:
            event["consumed"] = False
    if event.get("destination_consumed") is True:
        event["consumed"] = True
    return event


def run(args: argparse.Namespace) -> dict[str, Any]:
    output = Path(args.output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    topology = json.loads(Path(args.topology_file).read_text())
    versions = json.loads(Path(args.versions_file).read_text())
    compatibility = CompatibilitySpec(
        **{key: versions[key] for key in CompatibilitySpec.__dataclass_fields__}
    )
    versions = {**versions, "prefix_identity_version": "kv-prefix-identity-v1"}
    run_id = args.run_id or datetime.now(UTC).strftime("kv-hop-%Y%m%dT%H%M%SZ")
    started = datetime.now(UTC).isoformat().replace("+00:00", "Z")

    for url in (args.worker_a_url, args.worker_b_url):
        health = json.loads(_get(f"{url.rstrip('/')}/health"))
        if health.get("status") not in {"ok", "healthy"}:
            raise RuntimeError("both workers must be healthy before the smoke")

    deployments = json.loads(
        _kubectl(
            args.namespace,
            "get",
            "deploy/inference-worker-a",
            "deploy/inference-worker-b",
            "deploy/mooncake",
            "-o",
            "json",
        )
    )
    (output / "deployments.json").write_text(json.dumps(deployments, indent=2) + "\n")

    prefix_sizes = getattr(args, "prefix_sizes", None) or [getattr(args, "prefix_size", "4k")]
    records: dict[str, dict[str, Any]] = {}

    def invoke(case: str, worker: str, hop: str, *, target: bool, size: str = "4k") -> str:
        tag = f"{case}-{size}" if len(prefix_sizes) > 1 else case
        request_id = f"{run_id}-{tag}-{'target' if target else uuid.uuid4().hex[:8]}"
        worker_url = args.worker_a_url if worker == "worker_a" else args.worker_b_url
        before = _get(f"{worker_url.rstrip('/')}/metrics")
        t0 = time.perf_counter()
        response = _post(
            f"{args.gateway_url.rstrip('/')}/v1/chat/completions",
            {
                "model": versions["model_id"],
                "messages": [{"role": "user", "content": _prompt(run_id, case, size)}],
                "temperature": 0,
                "max_tokens": 1,
                "stream": False,
            },
            _request_headers(case, request_id, worker, hop),
        )
        e2e_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        after = _get(f"{worker_url.rstrip('/')}/metrics")
        record = {
            "request_id": request_id,
            "case": case,
            "prefix_size": size,
            "target_tokens": E5_PREFIX_SIZES.get(size, 4096),
            "worker": worker,
            "hop_mode": hop,
            "response": response,
            "e2e_latency_ms": e2e_ms,
            "ttft_ms": _ttft_ms(before, after),
            "prefix_cache_hits_before": _prefix_hits(before),
            "prefix_cache_hits_after": _prefix_hits(after),
        }
        (output / f"{request_id}.json").write_text(json.dumps(record, indent=2) + "\n")
        (output / f"{request_id}.metrics.before.prom").write_text(before)
        (output / f"{request_id}.metrics.after.prom").write_text(after)
        if target:
            records[tag] = record
        return request_id

    for size in prefix_sizes:
        invoke(CASES[0], "worker_a", "off", target=False, size=size)
        invoke(CASES[0], "worker_a", "off", target=True, size=size)
        invoke(CASES[1], "worker_a", "off", target=False, size=size)
        invoke(CASES[1], "worker_b", "off", target=True, size=size)
        invoke(CASES[2], "worker_a", "off", target=False, size=size)
        invoke(CASES[2], "worker_b", "off", target=False, size=size)
        invoke(CASES[2], "worker_b", "off", target=True, size=size)
        invoke(CASES[3], "worker_a", "off", target=False, size=size)
        invoke(CASES[3], "worker_b", "on", target=True, size=size)

    time.sleep(1)
    logs = ""
    for deployment in ("inference-worker-a", "inference-worker-b"):
        text = _kubectl(
            args.namespace,
            "logs",
            f"deployment/{deployment}",
            "-c",
            "vllm",
            f"--since-time={started}",
        )
        (output / f"{deployment}.log").write_text(text)
        logs += text + "\n"
    parsed = _json_events(logs)
    (output / "kv-events.json").write_text(json.dumps(parsed, indent=2) + "\n")

    all_cases: list[dict[str, Any]] = []

    for size in prefix_sizes:
        for case in CASES:
            case_name = f"{case}@{size}" if len(prefix_sizes) > 1 else case
            tag = f"{case}-{size}" if len(prefix_sizes) > 1 else case
            record = records[tag]
            event = _target_event(
                case,
                record["request_id"],
                parsed,
                record["response"],
                record["prefix_cache_hits_after"] - record["prefix_cache_hits_before"],
            )
            event["prefix_size"] = size
            all_cases.append({"name": case_name, "events": [event]})

    crossover: dict[str, dict[str, Any]] = {}
    for size in prefix_sizes:
        suffix = f"@{size}" if len(prefix_sizes) > 1 else ""
        recompute_case_name = f"{CASES[1]}{suffix}"
        transfer_case_name = f"{CASES[3]}{suffix}"
        recompute_tag = f"{CASES[1]}-{size}" if len(prefix_sizes) > 1 else CASES[1]
        transfer_tag = f"{CASES[3]}-{size}" if len(prefix_sizes) > 1 else CASES[3]
        recompute_rec = records.get(recompute_tag, {})
        transfer_rec = records.get(transfer_tag, {})
        recompute_event = next(
            c["events"][0] for c in all_cases if c["name"] == recompute_case_name
        )
        transfer_event = next(
            c["events"][0] for c in all_cases if c["name"] == transfer_case_name
        )
        crossover[size] = {
            "size_label": size,
            "target_tokens": E5_PREFIX_SIZES.get(size, 4096),
            "actual_reusable_tokens": transfer_event.get("reusable_tokens"),
            "transferred_tokens": transfer_event.get("transferred_tokens"),
            "transferred_bytes": transfer_event.get("transferred_bytes"),
            "transfer_ms": transfer_event.get("transfer_ms"),
            "lookup_ms": transfer_event.get("lookup_ms"),
            "confirm_ms": transfer_event.get("confirm_ms"),
            "destination_consumed": transfer_event.get("destination_consumed"),
            "recompute_request_id": recompute_event.get("request_id"),
            "transfer_request_id": transfer_event.get("request_id"),
            "recompute_lookup_ms": recompute_event.get("lookup_ms"),
            "recompute_ttft_ms": recompute_rec.get("ttft_ms"),
            "recompute_e2e_ms": recompute_rec.get("e2e_latency_ms"),
            "transfer_ttft_ms": transfer_rec.get("ttft_ms"),
            "transfer_e2e_ms": transfer_rec.get("e2e_latency_ms"),
            "recompute_event": recompute_event,
            "transfer_event": transfer_event,
        }
    (output / "crossover.json").write_text(json.dumps(crossover, indent=2) + "\n")

    manifest = {
        "run_id": run_id,
        "started_at": started,
        "topology": topology,
        "versions": versions,
        "compatibility_namespace": compatibility.namespace,
        "prefix_sizes": prefix_sizes,
        "target_prefix_tokens": {s: E5_PREFIX_SIZES[s] for s in prefix_sizes},
        "control_isolation_statement": (
            "Each case used a unique leading marker; each metrics window contained one target "
            "request, and worker placement plus hop mode were forced by gated lab controls."
        ),
        "deployments_artifact": "deployments.json",
        "crossover_artifact": "crossover.json",
    }
    validation = validate_run(manifest, all_cases)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output / "cases.json").write_text(json.dumps(all_cases, indent=2) + "\n")
    (output / "validation.json").write_text(json.dumps(validation, indent=2) + "\n")
    return validation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway-url", required=True)
    parser.add_argument("--worker-a-url", required=True)
    parser.add_argument("--worker-b-url", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--topology-file", required=True)
    parser.add_argument("--versions-file", required=True)
    parser.add_argument("--namespace", default="inference-lab")
    parser.add_argument(
        "--prefix-size",
        default="4k",
        choices=list(E5_PREFIX_SIZES.keys()),
        help="Default E5 prefix size for the four-case proof",
    )
    parser.add_argument(
        "--prefix-sizes",
        nargs="+",
        choices=list(E5_PREFIX_SIZES.keys()),
        help="Evaluate multiple E5 prefix sizes (1k, 2k, 4k, 7k) for crossover analysis",
    )
    parser.add_argument("--run-id")
    print(json.dumps(run(parser.parse_args()), indent=2))


if __name__ == "__main__":
    main()
