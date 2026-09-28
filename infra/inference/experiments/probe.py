"""Small standard-library probe for one directly reachable vLLM worker."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_REQUEST_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class WorkerProbeResult:
    """Evidence retained from the one completion and metrics probe."""

    model: str
    completion_text: str
    completion_usage: dict[str, Any]
    raw_metrics: str


def _endpoint(base_url: str, path: str) -> str:
    if not isinstance(base_url, str) or not base_url.startswith(
        ("http://", "https://")
    ):
        raise ValueError("base_url must be an HTTP(S) URL")
    return f"{base_url.rstrip('/')}{path}"


def _request(endpoint: str, *, body: Mapping[str, Any] | None = None) -> bytes:
    data = None
    headers: dict[str, str] = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["content-type"] = "application/json"
    request = Request(
        endpoint, data=data, headers=headers, method="POST" if data else "GET"
    )
    try:
        with urlopen(
            request, timeout=_REQUEST_TIMEOUT_SECONDS
        ) as response:  # noqa: S310 - explicit probe URL
            return response.read()
    except HTTPError as exc:
        raise RuntimeError(
            f"worker probe request to {endpoint} returned HTTP {exc.code}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(
            f"worker probe request to {endpoint} failed: {exc.reason}"
        ) from exc


def _json_response(
    endpoint: str, *, body: Mapping[str, Any] | None = None
) -> Mapping[str, Any]:
    try:
        value = json.loads(_request(endpoint, body=body))
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"worker probe request to {endpoint} returned invalid JSON"
        ) from exc
    if not isinstance(value, Mapping):
        raise RuntimeError(
            f"worker probe request to {endpoint} returned a non-object JSON response"
        )
    return value


def probe_worker(
    *, base_url: str, model: str, prompt: str, max_tokens: int
) -> WorkerProbeResult:
    """Call the worker's direct health, model, completion, and metrics endpoints."""
    if not isinstance(model, str) or not model:
        raise ValueError("model must be a non-empty string")
    if not isinstance(prompt, str):
        raise ValueError("prompt must be a string")
    if (
        isinstance(max_tokens, bool)
        or not isinstance(max_tokens, int)
        or max_tokens <= 0
    ):
        raise ValueError("max_tokens must be a positive integer")

    _request(_endpoint(base_url, "/health"))
    advertised_models = _json_response(_endpoint(base_url, "/v1/models")).get("data")
    if not isinstance(advertised_models, list) or model not in {
        entry.get("id") for entry in advertised_models if isinstance(entry, Mapping)
    }:
        models_url = _endpoint(base_url, "/v1/models")
        raise RuntimeError(f"requested model {model!r} is not served by {models_url}")

    completion = _json_response(
        _endpoint(base_url, "/v1/completions"),
        body={"model": model, "prompt": prompt, "max_tokens": max_tokens},
    )
    choices = completion.get("choices")
    usage = completion.get("usage")
    if (
        not isinstance(choices, list)
        or not choices
        or not isinstance(choices[0], Mapping)
        or not isinstance(choices[0].get("text"), str)
        or not isinstance(usage, Mapping)
    ):
        raise RuntimeError(
            "worker completion response is missing choices text or usage"
        )

    raw_metrics = _request(_endpoint(base_url, "/metrics")).decode("utf-8")
    return WorkerProbeResult(
        model=model,
        completion_text=choices[0]["text"],
        completion_usage=dict(usage),
        raw_metrics=raw_metrics,
    )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Probe a vLLM worker")
    parser.add_argument("--base-url", required=True, help="Worker base URL")
    parser.add_argument("--model", required=True, help="Expected model name")
    parser.add_argument("--prompt", default="Ready probe.", help="Prompt string")
    parser.add_argument("--max-tokens", type=int, default=5, help="Max tokens")
    parser.add_argument(
        "--output-file",
        default=None,
        help="Optional JSONL file to append probe response record",
    )
    args = parser.parse_args(argv)

    result = probe_worker(
        base_url=args.base_url,
        model=args.model,
        prompt=args.prompt,
        max_tokens=args.max_tokens,
    )
    print(f"Probe succeeded for {args.base_url}:")
    print(f"  Model: {result.model}")
    print(f"  Completion: {result.completion_text.strip()!r}")
    print(f"  Usage: {result.completion_usage}")

    if args.output_file:
        out_path = Path(args.output_file)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": time.time(),
            "endpoint": args.base_url,
            "model": result.model,
            "completion": result.completion_text,
            "usage": result.completion_usage,
        }
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
