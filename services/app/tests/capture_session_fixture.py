"""Records one multi-turn taxi session through the REAL loop, policy layer and capture.

Everything between the loop and the HTTP boundary is production code (OrchestrationLoop,
LiteLLMClient + model policy/pricing, CaptureRecorder). Only the model provider and the MCP
dataset server are scripted, so the numbers in the answers are NOT live Bedrock output.
Regenerate the committed fixture with:

    uv run --project services/app python services/app/tests/capture_session_fixture.py OUT.jsonl
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
from app.capture.recorder import CaptureRecorder, CaptureSettings
from app.llm import LiteLLMClient
from app.orchestration import OrchestrationLoop
from app.state import InMemoryStateRepository

TURNS = [
    "Which pickup zones have the most trips?",
    "Compare that with the second highest zone",
]
_ZONES = [["JFK Airport", 145240], ["Upper East Side South", 141266]]
_USAGE = [
    (412, 38),
    (655, 61),
    (503, 41),
    (902, 74),
]  # scripted (in, out) per model call


class ScriptedMCP:
    def get_dataset_schema(self, **_: Any) -> dict[str, Any]:
        return {
            "dataset": "nyc-taxi",
            "month": "2024-01",
            "columns": ["PULocationID", "fare_amount"],
        }

    def query_taxi_data(
        self, analysis: str, limit: int = 5, **_: Any
    ) -> dict[str, Any]:
        return {
            "columns": ["pickup_zone", "trip_count"],
            "rows": _ZONES,
            "row_count": 2,
            "execution_duration_ms": 12,
            "query_id": "scripted-q1",
            "truncated": False,
        }

    def compare_taxi_segments(self, **_: Any) -> dict[str, Any]:
        return {
            "columns": ["segment", "trip_count"],
            "rows": [["JFK Airport", 145240], ["Upper East Side South", 141266]],
            "row_count": 2,
            "execution_duration_ms": 18,
            "query_id": "scripted-q2",
            "truncated": False,
        }


def _provider(calls: list[int]):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        time.sleep(0.03)  # scripted provider delay so measured latency is non-zero
        n = len(calls)
        calls.append(n)
        usage = _USAGE[n % len(_USAGE)]
        second = "second highest" in json.dumps(body["messages"]).lower()
        if body.get("tools"):
            if second:
                args = {
                    "segment_dimension": "pickup_zone",
                    "measures": ["trip_count"],
                    "baseline_filters": {"pickup_zone": "JFK Airport"},
                    "comparison_filters": {"pickup_zone": "Upper East Side South"},
                    "limit": 5,
                }
                name = "compare_taxi_segments"
            else:
                args = {"analysis": "top_pickup_zones", "limit": 5}
                name = "query_taxi_data"
            message: dict[str, Any] = {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": name, "arguments": json.dumps(args)}}
                ],
            }
        else:
            text = (
                "Upper East Side South is second with 141266 trips, about 3.9 thousand "
                "fewer than JFK Airport."
                if second
                else "JFK Airport has the most pickups with 145240 trips."
            )
            message = {"role": "assistant", "content": text}
        return httpx.Response(
            200,
            json={
                "model": body["model"],
                "choices": [{"message": message, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]},
            },
        )

    return handler


def record_session(capture_dir: Path) -> Path:
    recorder = CaptureRecorder(
        CaptureSettings(directory=capture_dir, include_content=True),
        tenant_id="default",
    )
    client = LiteLLMClient(
        "http://litellm:4000",
        http_client=httpx.Client(transport=httpx.MockTransport(_provider([]))),
    )
    loop = OrchestrationLoop(
        llm_client=client,
        mcp_client=ScriptedMCP(),  # type: ignore[arg-type]
        state_repository=InMemoryStateRepository(),
        capture=recorder,
    )
    conversation_id = None
    for question in TURNS:
        result = loop.run(question, conversation_id)
        conversation_id = result.conversation_id
    return capture_dir / "default" / "captures.jsonl"


if __name__ == "__main__":
    out = Path(sys.argv[1])
    with tempfile.TemporaryDirectory() as tmp:
        src = record_session(Path(tmp))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(src.read_text())
