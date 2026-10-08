#!/usr/bin/env python3
"""Smoke test verifying the serve path end-to-end through the inference gateway."""

import os
import sys
import time

import httpx
from app.benchmarks.canonical_prefix import CANONICAL_TAXI_SCHEMA
from app.benchmarks.serve_smoke import problems_for_accepted, problems_for_rejected
from app.llm import ServeLLMClient


def _post_gateway(gateway_url: str, body: dict, request_id: str) -> httpx.Response:
    headers = {
        "x-request-id": request_id,
        "x-conversation-id": f"conv-{request_id}",
        "x-agent-step": "1",
        "x-prefix-id": "prefix-serve-smoke",
    }
    return httpx.post(gateway_url, json=body, headers=headers, timeout=60.0)


def _fail_on(label: str, problems: list[str]) -> None:
    if problems:
        print(f"[FAIL] {label}: " + "; ".join(problems))
        sys.exit(1)
    print(f"[OK] {label}")


def main() -> None:
    gateway_url = os.getenv("INFERENCE_GATEWAY_URL", "http://127.0.0.1:18080/serve")
    model_id = os.getenv("INFERENCE_MODEL_ID", "Qwen/Qwen3-0.6B")

    base_url = gateway_url.removesuffix("/serve")
    health_url = f"{base_url}/health"

    print("== Inference Serve Path Smoke ==")
    print(f"Target Gateway URL: {gateway_url}")
    print(f"Target Model:       {model_id}")

    # 1. Health check
    try:
        resp = httpx.get(health_url, timeout=5.0)
        resp.raise_for_status()
        print(f"[OK] Gateway Health Check: {resp.status_code} {resp.json()}")
    except Exception as exc:
        print(f"[FAIL] Could not reach gateway at {health_url}: {exc}")
        print(
            "Ensure SSH tunnel to Lambda gateway is active: make inference-tunnel or ssh"
        )
        sys.exit(1)

    # 2. Synchronous model ask through ServeLLMClient
    print("\n--- Test 1: Direct Ask via ServeLLMClient ---")
    client = ServeLLMClient(
        gateway_url=gateway_url,
        model_id=model_id,
        timeout_seconds=30.0,
    )

    t0 = time.perf_counter()
    prompt = "What is the average yellow taxi trip distance in Manhattan?"
    conv_id = f"smoke-conv-{int(time.time())}"

    try:
        result = client.ask(prompt=prompt, conversation_id=conv_id)
        latency = (time.perf_counter() - t0) * 1000
        print(f"[OK] Response received in {latency:.1f}ms")
        print(f"     Prompt tokens:     {result.input_tokens}")
        print(f"     Completion tokens: {result.output_tokens}")
        if result.output_tokens > 0:
            speed = result.output_tokens / (latency / 1000.0)
            print(f"     Throughput:        {speed:.1f} tokens/s")
        print(f"     Text snippet:      {result.text[:120]}...")
    except Exception as exc:
        print(f"[FAIL] Ask failed: {exc}")
        sys.exit(1)

    # 3. Streaming model answer with SSE
    print("\n--- Test 2: Streaming Answer via ServeLLMClient ---")
    query_result = {
        "columns": ["pulocationid", "trip_count"],
        "rows": [{"pulocationid": 237, "trip_count": 15420}],
        "row_count": 1,
        "execution_duration_ms": 12,
        "query_id": "qry-smoke-01",
        "truncated": False,
    }
    deltas: list[str] = []
    t_start = time.perf_counter()
    first_token_time = None

    def on_delta(delta: str) -> None:
        nonlocal first_token_time
        if first_token_time is None:
            first_token_time = time.perf_counter()
        deltas.append(delta)

    try:
        stream_res = client.stream_answer_with_query_result(
            prompt="Which pickup location had the most trips?",
            query_result=query_result,
            delta_callback=on_delta,
            conversation_id=conv_id,
        )
        total_time = (time.perf_counter() - t_start) * 1000
        ttft = ((first_token_time - t_start) * 1000) if first_token_time else total_time

        print(f"[OK] Streaming finished in {total_time:.1f}ms (TTFT: {ttft:.1f}ms)")
        print(f"     Output tokens:     {stream_res.output_tokens}")
        if stream_res.output_tokens > 0:
            speed = stream_res.output_tokens / (total_time / 1000.0)
            print(f"     Throughput:        {speed:.1f} tokens/s")
        print(f"     Streamed text:     {''.join(deltas)[:120]}...")
    except Exception as exc:
        print(f"[FAIL] Streaming failed: {exc}")
        sys.exit(1)

    # 4. Every stage ran: the gateway stamps one decision header per stage on an accepted request.
    print("\n--- Test 3: Stage headers on an accepted request ---")
    ok_body = {
        "model": model_id,
        "messages": [{"role": "user", "content": "Reply with the word OK."}],
        "max_tokens": 8,
        "stream": False,
    }
    resp = _post_gateway(gateway_url, ok_body, f"smoke-ok-{int(time.time())}")
    problems = (
        [] if resp.status_code == 200 else [f"status {resp.status_code}, expected 200"]
    )
    _fail_on("accepted request", problems + problems_for_accepted(dict(resp.headers)))

    # 5. A tool-using agent step goes through the same path and comes back with a tool call.
    print("\n--- Test 4: Tool-calling agent step ---")
    try:
        proposal = client.propose_taxi_query(
            "Which pickup zones have the most trips?", CANONICAL_TAXI_SCHEMA
        )
    except Exception as exc:
        print(f"[FAIL] Tool proposal failed: {exc}")
        sys.exit(1)
    _fail_on(
        "tool call returned",
        (
            []
            if proposal.name and proposal.arguments
            else ["empty tool name or arguments"]
        ),
    )

    # 6. Rejections are stamped at the stage that made them, and never reach a worker.
    print("\n--- Test 5: Guard rejects ---")
    too_long = {**ok_body, "messages": [{"role": "user", "content": "x" * 200_000}]}
    resp = _post_gateway(gateway_url, too_long, f"smoke-long-{int(time.time())}")
    _fail_on(
        "oversized prompt rejected",
        problems_for_rejected(
            resp.status_code,
            dict(resp.headers),
            expected_status=413,
            decision_prefix="reject:",
        ),
    )
    over_window = {**ok_body, "messages": [{"role": "user", "content": "x" * 32_000}]}
    over_window["max_tokens"] = (
        500  # about 8,000 prompt + 500 completion tokens > 8,192 window
    )
    resp = _post_gateway(gateway_url, over_window, f"smoke-window-{int(time.time())}")
    _fail_on(
        "prompt + max_tokens over the context window rejected",
        problems_for_rejected(
            resp.status_code,
            dict(resp.headers),
            expected_status=413,
            decision_prefix="reject:",
        ),
    )

    print("\n== Inference Serve Path Smoke Passed Successfully ==")


if __name__ == "__main__":
    main()
