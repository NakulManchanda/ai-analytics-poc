"""Bounded CrewAI sequencing behind the application's owned LLM boundary."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.llm import LLMClient, LLMResult

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")

from crewai import Agent, Crew, Process, Task  # noqa: E402
from crewai.llm import BaseLLM  # noqa: E402


@dataclass(frozen=True)
class CrewAnswer:
    text: str
    calls: tuple[LLMResult, ...]


class GatewayLLM(BaseLLM):
    """Adapt CrewAI calls to the application's existing ``LLMClient``."""

    def __init__(
        self,
        llm_client: LLMClient,
        *,
        on_call: Callable[[LLMResult], None],
    ) -> None:
        super().__init__(model=llm_client.model_id)
        self._llm_client = llm_client
        self._on_call = on_call

    def call(
        self,
        messages: str | list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        callbacks: list[Any] | None = None,
        available_functions: dict[str, Any] | None = None,
        from_task: Any | None = None,
        from_agent: Any | None = None,
        response_model: Any | None = None,
    ) -> str:
        del callbacks, available_functions, from_task, from_agent, response_model
        if tools:
            raise ValueError("CrewAI strategy does not expose tools to the model")
        if isinstance(messages, str):
            prompt = messages
        else:
            prompt = "\n\n".join(
                f"{message.get('role', 'user')}: {message.get('content', '')}"
                for message in messages
            )
        result = self._llm_client.ask(prompt)
        self._on_call(result)
        return result.text

    def supports_function_calling(self) -> bool:
        return False


def run_two_agent_answer(
    *,
    llm_client: LLMClient,
    question: str,
    governed_result: dict[str, object],
    on_call: Callable[[LLMResult], None] | None = None,
) -> CrewAnswer:
    """Run one researcher and one writer with no delegation or memory."""
    import json

    calls: list[LLMResult] = []

    def _record_call(result: LLMResult) -> None:
        calls.append(result)
        if on_call is not None:
            on_call(result)

    gateway = GatewayLLM(llm_client, on_call=_record_call)
    researcher = Agent(
        role="Researcher",
        goal="Summarize the governed query result in one or two factual sentences.",
        backstory="Only restate numbers from the supplied governed result.",
        llm=gateway,
        max_iter=1,
        verbose=False,
        allow_delegation=False,
    )
    writer = Agent(
        role="Writer",
        goal="Answer the user's question using only the research summary.",
        backstory="You write one short, direct answer and never reveal reasoning.",
        llm=gateway,
        max_iter=1,
        verbose=False,
        allow_delegation=False,
    )
    research_task = Task(
        description=(
            "Governed query result: "
            f"{json.dumps(governed_result, separators=(',', ':'), sort_keys=True)}\n\n"
            f"User question: {question}\n\n"
            "Summarize only the relevant facts from the result."
        ),
        expected_output="One or two factual sentences.",
        agent=researcher,
    )
    write_task = Task(
        description=(
            f"User question: {question}\n\n"
            "Using only the research summary, write the final user-facing answer."
        ),
        expected_output="A short direct answer.",
        agent=writer,
        context=[research_task],
    )
    output = Crew(
        agents=[researcher, writer],
        tasks=[research_task, write_task],
        process=Process.sequential,
        memory=False,
        verbose=False,
    ).kickoff()
    if len(calls) != 2:
        raise RuntimeError(f"expected exactly 2 CrewAI model calls, got {len(calls)}")
    return CrewAnswer(text=str(output).strip(), calls=tuple(calls))
