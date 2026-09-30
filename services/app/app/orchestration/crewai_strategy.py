"""Bounded CrewAI sequencing behind the application's owned LLM boundary."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.llm import LLMClient, LLMResult
from app.prefix import (
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_TAXI_RULES,
    PrefixPartition,
    create_prefix_partition,
)

os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")

from crewai import Agent, Crew, Process, Task  # noqa: E402
from crewai.llm import BaseLLM  # noqa: E402


@dataclass(frozen=True)
class CrewAnswer:
    text: str
    calls: tuple[LLMResult, ...]


class GatewayLLM(BaseLLM):
    """Adapt CrewAI calls to the application's existing ``LLMClient`` or invocation hook."""

    def __init__(
        self,
        target: LLMClient | Callable[..., LLMResult],
        *,
        on_call: Callable[[LLMResult], None] | None = None,
        role: str = "Agent",
        model_id: str | None = None,
    ) -> None:
        resolved_model = model_id or getattr(target, "model_id", "default-model")
        super().__init__(model=resolved_model)
        self._target = target
        self._on_call = on_call
        self._role = role

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
        if callable(self._target) and not hasattr(self._target, "model_id"):
            result = self._target(self._role, messages)
        else:
            if isinstance(messages, str):
                prompt = messages
            else:
                prompt = "\n\n".join(
                    f"{message.get('role', 'user')}: {message.get('content', '')}"
                    for message in messages
                )
            result = self._target.ask(prompt)  # type: ignore[union-attr]
        if self._on_call is not None:
            self._on_call(result)
        return result.text

    def supports_function_calling(self) -> bool:
        return False


def run_two_agent_answer(
    *,
    question: str,
    governed_result: dict[str, object],
    llm_client: LLMClient | None = None,
    invoke_model: Callable[[str, PrefixPartition, int], LLMResult] | None = None,
    conversation_id: str | None = None,
    start_step: int = 2,
    on_call: Callable[[LLMResult], None] | None = None,
) -> CrewAnswer:
    """Run one researcher and one writer with no delegation or memory,
    using correlated growing prefixes.
    """
    if invoke_model is None:
        if llm_client is None:
            raise ValueError("Either invoke_model or llm_client must be provided")

        def _default_invoke(
            role: str, partition: PrefixPartition, agent_step: int
        ) -> LLMResult:
            del role
            if hasattr(llm_client, "execute_partitioned_call") and callable(
                llm_client.execute_partitioned_call
            ):
                return llm_client.execute_partitioned_call(
                    partition=partition,
                    conversation_id=conversation_id,
                    agent_step=agent_step,
                )
            user_content = (
                f"{partition.conversation_shared}\n\n{partition.unique_suffix}".strip()
            )
            prompt_text = f"{partition.global_shared}\n\n{user_content}".strip()
            return llm_client.ask(prompt_text)

        active_invoke = _default_invoke
        model_id = llm_client.model_id
    else:
        active_invoke = invoke_model
        model_id = getattr(llm_client, "model_id", "default-model")

    governed_result_json = json.dumps(
        governed_result, separators=(",", ":"), sort_keys=True
    )
    global_shared = f"{DEFAULT_SYSTEM_PROMPT}\n{DEFAULT_TAXI_RULES}"
    research_summary = ""
    calls: list[LLMResult] = []

    def _invoke_for_role(
        role: str, raw_messages: str | list[dict[str, Any]]
    ) -> LLMResult:
        nonlocal research_summary
        if isinstance(raw_messages, str):
            prompt_str = raw_messages
        else:
            prompt_str = "\n\n".join(
                f"{m.get('role', 'user')}: {m.get('content', '')}" for m in raw_messages
            )

        if role.lower() == "researcher":
            step = start_step
            partition = create_prefix_partition(
                global_shared=global_shared,
                conversation_shared=f"Observation (query_result): {governed_result_json}",
                unique_suffix=prompt_str,
            )
        else:
            step = start_step + 1
            writer_conv = (
                f"Observation (query_result): {governed_result_json}\n\n"
                f"Research summary: {research_summary}"
            )
            partition = create_prefix_partition(
                global_shared=global_shared,
                conversation_shared=writer_conv,
                unique_suffix=prompt_str,
            )

        result = active_invoke(role, partition, step)
        calls.append(result)
        if role.lower() == "researcher":
            research_summary = result.text
        if on_call is not None:
            on_call(result)
        return result

    researcher_llm = GatewayLLM(_invoke_for_role, role="Researcher", model_id=model_id)
    writer_llm = GatewayLLM(_invoke_for_role, role="Writer", model_id=model_id)

    researcher = Agent(
        role="Researcher",
        goal="Summarize the governed query result in one or two factual sentences.",
        backstory="Only restate numbers from the supplied governed result.",
        llm=researcher_llm,
        max_iter=1,
        max_retry_limit=0,
        verbose=False,
        allow_delegation=False,
    )
    writer = Agent(
        role="Writer",
        goal="Answer the user's question using only the research summary.",
        backstory="You write one short, direct answer and never reveal reasoning.",
        llm=writer_llm,
        max_iter=1,
        max_retry_limit=0,
        verbose=False,
        allow_delegation=False,
    )
    research_task = Task(
        description=(
            "Governed query result: "
            f"{governed_result_json}\n\n"
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
