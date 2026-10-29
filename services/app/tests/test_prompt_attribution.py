"""#178: every model call is attributed to the prompt revision it actually rendered."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from app.capture.recorder import CaptureRecorder, CaptureSettings
from app.llm import (
    BedrockLLMClient,
    LiteLLMClient,
    LLMProviderError,
    LocalFakeLLMClient,
    ServeLLMClient,
)
from app.orchestration import OrchestrationLoop
from app.prefix import PrefixPartition
from app.state import InMemoryStateRepository
from capture_session_fixture import TURNS, ScriptedMCP, _provider
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from test_orchestration_loop import FakeMCPClient

SHA = lambda text: hashlib.sha256(text.encode()).hexdigest()  # noqa: E731


def _records(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text().splitlines()]


def _session(tmp_path: Path, *, content: bool) -> tuple[list[dict], list[dict]]:
    """Two-turn session through the real loop + LiteLLMClient; also returns each
    request body the provider saw."""
    bodies: list[dict] = []
    inner = _provider([])

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return inner(request)

    recorder = CaptureRecorder(
        CaptureSettings(directory=tmp_path, include_content=content)
    )
    loop = OrchestrationLoop(
        llm_client=LiteLLMClient(
            "http://litellm:4000",
            http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        ),
        mcp_client=ScriptedMCP(),  # type: ignore[arg-type]
        state_repository=InMemoryStateRepository(),
        capture=recorder,
    )
    conversation_id = None
    for question in TURNS:
        conversation_id = loop.run(question, conversation_id).conversation_id
    records = _records(tmp_path / "default" / "captures.jsonl")
    return [r for r in records if r["record_type"] == "model_call"], bodies


@pytest.mark.parametrize("content", [False, True])
def test_two_turn_session_attributes_each_call_to_its_own_rendered_prompt(
    tmp_path: Path, content: bool
) -> None:
    calls, bodies = _session(tmp_path, content=content)
    calls.sort(key=lambda r: (r["run_started_at"], r["step"]))
    # Turn 1 is a catalogue hit (no planner model call); turn 2 plans and answers.
    assert [c["role"] for c in calls] == ["answer", "planner", "answer"]
    assert [c["prompt_template_id"] for c in calls] == [
        "answer",
        "planner-single-turn",
        "answer",
    ]
    assert [c["prompt_template_version"] for c in calls] == ["1.0.0", "1.2.0", "1.0.0"]
    # No run-wide tag masquerades as a template version.
    assert all(
        c["prompt_template_version"] != c["prompt_config_version"] for c in calls
    )
    assert {c["prompt_config_version"] for c in calls} == {"m9.v1"}

    assert len(bodies) == 3
    # Answer: the system message is the rendered instruction region, reproducible, and
    # identical across turns (user content and history are not part of the hash).
    assert calls[0]["prompt_sha256"] == SHA(bodies[0]["messages"][0]["content"])
    assert calls[2]["prompt_sha256"] == SHA(bodies[2]["messages"][0]["content"])
    assert calls[0]["prompt_sha256"] == calls[2]["prompt_sha256"]
    # Planner: the single user message IS the rendered template.
    assert calls[1]["prompt_sha256"] == SHA(bodies[1]["messages"][-1]["content"])
    # Rendered text is never captured, with or without CAPTURE_CONTENT.
    blob = json.dumps(calls)
    assert "Choose exactly ONE" not in blob and "NYC Taxi Analytics" not in blob


def test_run_state_carries_per_call_attribution(tmp_path: Path) -> None:
    repo = InMemoryStateRepository()
    loop = OrchestrationLoop(
        llm_client=LiteLLMClient(
            "http://litellm:4000",
            http_client=httpx.Client(transport=httpx.MockTransport(_provider([]))),
        ),
        mcp_client=ScriptedMCP(),  # type: ignore[arg-type]
        state_repository=repo,
    )
    result = loop.run(TURNS[1])
    ids = [(c.role, c.prompt_template_id) for c in result.llm_calls]
    assert ids == [("planner", "planner-single-turn"), ("answer", "answer")]
    run = repo.get_run(result.run_id)
    stored = {c["role"]: c for c in run.metadata["llm_calls"]}
    assert stored["planner"]["prompt_sha256"] != stored["answer"]["prompt_sha256"]
    steps = {s.llm_call_id: s for s in repo.list_run_steps(result.run_id)}
    for call in result.llm_calls:
        assert steps[call.llm_call_id].metadata["prompt_template_id"] == (
            call.prompt_template_id
        )


def test_otel_event_per_call_carries_prompt_attributes() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    loop = OrchestrationLoop(
        llm_client=LiteLLMClient(
            "http://litellm:4000",
            http_client=httpx.Client(transport=httpx.MockTransport(_provider([]))),
        ),
        mcp_client=ScriptedMCP(),  # type: ignore[arg-type]
        state_repository=InMemoryStateRepository(),
        tracer=provider.get_tracer("t"),
    )
    loop.run(TURNS[1])
    (span,) = exporter.get_finished_spans()
    events = [e for e in span.events if e.name == "ai.llm_call"]
    assert [e.attributes["ai.llm.role"] for e in events] == ["planner", "answer"]
    assert [e.attributes["ai.prompt.template_id"] for e in events] == [
        "planner-single-turn",
        "answer",
    ]
    assert all(len(e.attributes["ai.prompt.sha256"]) == 64 for e in events)
    assert [e.attributes["ai.prompt.template_version"] for e in events] == [
        "1.2.0",
        "1.0.0",
    ]


def test_catalogue_path_has_no_planner_call_to_attribute(tmp_path: Path) -> None:
    from app.query_catalogue import lookup

    question = TURNS[0]
    assert lookup(question) is not None
    recorder = CaptureRecorder(CaptureSettings(directory=tmp_path))
    loop = OrchestrationLoop(
        llm_client=LiteLLMClient(
            "http://litellm:4000",
            http_client=httpx.Client(transport=httpx.MockTransport(_provider([]))),
        ),
        mcp_client=ScriptedMCP(),  # type: ignore[arg-type]
        state_repository=InMemoryStateRepository(),
        capture=recorder,
    )
    result = loop.run(question)
    assert [c.role for c in result.llm_calls] == ["answer"]
    assert result.llm_calls[0].prompt_template_id == "answer"


def test_fake_client_makes_no_prompt_claims(tmp_path: Path) -> None:
    recorder = CaptureRecorder(CaptureSettings(directory=tmp_path))
    loop = OrchestrationLoop(
        llm_client=LocalFakeLLMClient(),
        mcp_client=FakeMCPClient(),  # type: ignore[arg-type]
        state_repository=InMemoryStateRepository(),
        capture=recorder,
    )
    loop.run("What are the top pickup zones by ride count?")
    models = [
        r
        for r in _records(tmp_path / "default" / "captures.jsonl")
        if r["record_type"] == "model_call"
    ]
    assert models
    assert all(
        m["prompt_template_id"] is None and m["prompt_sha256"] is None for m in models
    )
    assert all(m["prompt_template_version"] is None for m in models)


def test_failed_proposal_still_carries_the_rendered_revision() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [
                    {"message": {"content": "no tool"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1},
            },
        )

    client = LiteLLMClient(
        "http://litellm:4000",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(LLMProviderError) as info:
        client.propose_taxi_query("q", {"columns": ["a"]})
    assert info.value.code == "no_tool_call"
    assert info.value.prompt_attribution.template_id == "planner-single-turn"


def test_serve_client_attributes_partitioned_and_streaming_calls() -> None:
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        if body["stream"]:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=(
                    b'data: {"choices": [{"delta": {"content": "hi"}}]}\n\n'
                    b"data: [DONE]\n\n"
                ),
            )
        return httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    client = ServeLLMClient(
        "http://gw/serve",
        "m",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    streamed = client.stream_answer_with_query_result(
        "q", {"rows": []}, lambda _d: None
    )
    plain = client.answer_with_query_result("q", {"rows": []})
    assert streamed.prompt_attribution == plain.prompt_attribution
    assert streamed.prompt_attribution.template_id == "answer"
    assert streamed.prompt_attribution.sha256 == SHA(sent[0]["messages"][0]["content"])
    profile = client.answer_with_dataset_profile("q", {"row_count": 1})
    assert profile.prompt_attribution.template_id == "dataset-profile"
    ask = client.ask("hello")
    assert ask.prompt_attribution.template_id == "ask"
    assert ask.prompt_attribution.sha256 == SHA(sent[-1]["messages"][0]["content"])


class _Runtime:
    def __init__(self, tool: bool) -> None:
        self.requests: list[dict] = []
        self.tool = tool

    def converse(self, **request: Any) -> dict[str, Any]:
        self.requests.append(request)
        content: list[dict[str, Any]] = (
            [{"toolUse": {"name": "query_taxi_data", "input": {"analysis": "x"}}}]
            if request.get("toolConfig")
            else [{"text": "answer"}]
        )
        return {
            "output": {"message": {"content": content}},
            "usage": {"inputTokens": 1, "outputTokens": 1},
            "metrics": {"latencyMs": 1},
        }

    def converse_stream(self, **request: Any) -> dict[str, Any]:
        self.requests.append(request)
        return {
            "stream": [
                {"contentBlockDelta": {"delta": {"text": "x"}}},
                {
                    "metadata": {
                        "usage": {"inputTokens": 1, "outputTokens": 1},
                        "metrics": {"latencyMs": 1},
                    }
                },
            ]
        }


def test_bedrock_client_attributes_single_turn_prompts() -> None:
    runtime = _Runtime(tool=True)
    client = BedrockLLMClient("model", runtime_client=runtime)
    plan = client.propose_taxi_query("q", {"columns": ["a"]})
    assert plan.prompt_attribution.template_id == "planner-single-turn"
    sent = runtime.requests[0]["messages"][0]["content"][0]["text"]
    assert plan.prompt_attribution.sha256 == SHA(sent)
    answer = client.answer_with_query_result("q", {"rows": []})
    assert answer.prompt_attribution.template_id == "answer-single-turn"
    streamed = client.stream_answer_with_query_result(
        "q", {"rows": []}, lambda _d: None
    )
    assert streamed.prompt_attribution == answer.prompt_attribution
    profile = client.answer_with_dataset_profile("q", {"row_count": 1})
    assert profile.prompt_attribution.template_id == "dataset-profile-single-turn"
    part = PrefixPartition("g", "c", "u", "pid", 1, 1)
    assert client.execute_partitioned_call(part).prompt_attribution is None


def test_crewai_calls_are_attributed_to_the_agent_instruction_bundle() -> None:
    from app.llm import LLMResult
    from app.orchestration.crewai_strategy import run_two_agent_answer

    seen: list[PrefixPartition] = []

    def invoke(role: str, partition: PrefixPartition, step: int) -> LLMResult:
        seen.append(partition)
        return LLMResult("t", "m", 1, 1, 1)

    run_two_agent_answer(
        question="q", governed_result={"rows": []}, invoke_model=invoke
    )
    assert [p.attribution.template_id for p in seen] == ["agent-global"] * 2
    assert seen[0].attribution.sha256 == SHA(seen[0].global_shared)
