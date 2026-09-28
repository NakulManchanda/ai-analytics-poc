"""HTTP contracts for the isolated vLLM worker probe.

The production probe deliberately uses the standard-library HTTP client so it
can run inside the remote bundle without importing application code.  Importing
it inside each test preserves a useful RED failure while the module is absent.
"""

from __future__ import annotations

import importlib
import json
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _probe_module():
    return importlib.import_module("infra.inference.experiments.probe")


@contextmanager
def _fake_worker(
    *,
    served_models: tuple[str, ...] = ("demo-model",),
    failure: tuple[str, int] | None = None,
) -> Iterator[tuple[str, list[tuple[str, str, dict[str, Any] | None]]]]:
    """Serve the narrow OpenAI/vLLM surface that #120 independently smokes."""

    calls: list[tuple[str, str, dict[str, Any] | None]] = []

    class WorkerHandler(BaseHTTPRequestHandler):
        def _send_json(self, status: int, body: dict[str, Any]) -> None:
            encoded = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            calls.append(("GET", self.path, None))
            if failure and self.path == failure[0]:
                self._send_json(failure[1], {"error": "deliberate fake failure"})
                return
            if self.path == "/health":
                self._send_json(200, {"status": "ok"})
            elif self.path == "/v1/models":
                self._send_json(
                    200, {"data": [{"id": model} for model in served_models]}
                )
            elif self.path == "/metrics":
                metrics = b"vllm:num_requests_running 1\n"
                self.send_response(200)
                self.send_header("content-type", "text/plain; version=0.0.4")
                self.send_header("content-length", str(len(metrics)))
                self.end_headers()
                self.wfile.write(metrics)
            else:
                self._send_json(404, {"error": "unknown path"})

        def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
            content_length = int(self.headers["content-length"])
            body = json.loads(self.rfile.read(content_length))
            calls.append(("POST", self.path, body))
            if failure and self.path == failure[0]:
                self._send_json(failure[1], {"error": "deliberate fake failure"})
                return
            if self.path == "/v1/completions":
                self._send_json(
                    200,
                    {
                        "choices": [{"text": "worker-ready"}],
                        "usage": {
                            "prompt_tokens": 3,
                            "completion_tokens": 2,
                            "total_tokens": 5,
                        },
                    },
                )
            else:
                self._send_json(404, {"error": "unknown path"})

        def log_message(self, _format: str, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), WorkerHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        yield f"http://{host}:{port}", calls
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_probe_worker_smokes_all_required_vllm_endpoints_and_preserves_evidence():
    probe = _probe_module()

    with _fake_worker() as (base_url, calls):
        result = probe.probe_worker(
            base_url=base_url,
            model="demo-model",
            prompt="Say ready.",
            max_tokens=7,
        )

    assert [(method, path) for method, path, _body in calls] == [
        ("GET", "/health"),
        ("GET", "/v1/models"),
        ("POST", "/v1/completions"),
        ("GET", "/metrics"),
    ]
    assert calls[2][2] == {
        "model": "demo-model",
        "prompt": "Say ready.",
        "max_tokens": 7,
    }
    assert result.model == "demo-model"
    assert result.completion_text == "worker-ready"
    assert result.completion_usage == {
        "prompt_tokens": 3,
        "completion_tokens": 2,
        "total_tokens": 5,
    }
    assert result.raw_metrics == "vllm:num_requests_running 1\n"


def test_probe_worker_refuses_a_worker_that_does_not_advertise_the_requested_model():
    probe = _probe_module()

    with _fake_worker(served_models=("another-model",)) as (base_url, calls):
        with pytest.raises(
            RuntimeError, match=r"demo-model.*not served|not served.*demo-model"
        ):
            probe.probe_worker(
                base_url=base_url,
                model="demo-model",
                prompt="Say ready.",
                max_tokens=7,
            )

    assert [(method, path) for method, path, _body in calls] == [
        ("GET", "/health"),
        ("GET", "/v1/models"),
    ]


@pytest.mark.parametrize(
    "failed_path", ("/health", "/v1/models", "/v1/completions", "/metrics")
)
def test_probe_worker_names_the_failed_endpoint_and_status_for_non_2xx_responses(
    failed_path: str,
):
    probe = _probe_module()

    with _fake_worker(failure=(failed_path, 503)) as (base_url, _calls):
        with pytest.raises(
            RuntimeError, match=rf"{failed_path}.*503|503.*{failed_path}"
        ):
            probe.probe_worker(
                base_url=base_url,
                model="demo-model",
                prompt="Say ready.",
                max_tokens=7,
            )
