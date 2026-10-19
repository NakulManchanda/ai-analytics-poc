"""Judge metrics. Normal runs read recorded verdicts; live DeepEval judging is explicit opt-in.

Metric names (all scores in 0..1):
  answer_quality   semantic correctness/helpfulness vs the reviewed reference facts (GEval)
  tool_correctness expected vs actual tool calls (DeepEval ToolCorrectnessMetric, deterministic)
  faithfulness     answer claims supported by the validated evidence (FaithfulnessMetric)
  task_completion  did the run accomplish the user's task (TaskCompletionMetric)
  step_efficiency  ReAct steps not wasteful (StepEfficiencyMetric)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from evals.schema import Turn
from evals.traces import TurnView

METRIC_NAMES = (
    "answer_quality",
    "tool_correctness",
    "faithfulness",
    "task_completion",
    "step_efficiency",
)
LIVE_FLAG = "EVALS_LIVE_JUDGE"


class JudgeDisabled(RuntimeError):
    """Raised when a live judge call is requested without explicit opt-in."""


def turn_key(case_id: str, turn_index: int) -> str:
    return f"{case_id}#{turn_index}"


@dataclass
class RecordedJudge:
    """Replays previously recorded verdicts; never calls a model."""

    judge_model: str
    scores: dict[str, dict[str, float]]

    @classmethod
    def load(cls, path: Path) -> RecordedJudge:
        data = json.loads(path.read_text())
        return cls(data["judge_model"], data["scores"])

    def score(self, case_id: str, turn_index: int) -> dict[str, float] | None:
        return self.scores.get(turn_key(case_id, turn_index))


def live_enabled() -> bool:
    return os.environ.get(LIVE_FLAG) == "1"


def build_metrics(judge_model: str, threshold: float = 0.5) -> dict[str, object]:
    """Construct the five DeepEval metrics (requires the `judge` extra; no model call here)."""
    if not live_enabled():
        raise JudgeDisabled(f"set {LIVE_FLAG}=1 to construct live judge metrics")
    from deepeval.metrics import (
        FaithfulnessMetric,
        GEval,
        StepEfficiencyMetric,
        TaskCompletionMetric,
        ToolCorrectnessMetric,
    )
    from deepeval.test_case import SingleTurnParams

    return {
        "answer_quality": GEval(
            name="AnswerQuality",
            criteria=(
                "The actual output answers the question and its numbers agree with the "
                "retrieval context (reviewed reference facts). Penalise fabricated values."
            ),
            evaluation_params=[
                SingleTurnParams.INPUT,
                SingleTurnParams.ACTUAL_OUTPUT,
                SingleTurnParams.RETRIEVAL_CONTEXT,
            ],
            model=judge_model,
            threshold=threshold,
        ),
        "tool_correctness": ToolCorrectnessMetric(threshold=threshold),
        "faithfulness": FaithfulnessMetric(threshold=threshold, model=judge_model),
        "task_completion": TaskCompletionMetric(threshold=threshold, model=judge_model),
        "step_efficiency": StepEfficiencyMetric(threshold=threshold, model=judge_model),
    }


def live_score(turn: Turn, view: TurnView, judge_model: str) -> dict[str, float]:
    """Judge one turn with DeepEval. Costs money; only reachable with EVALS_LIVE_JUDGE=1.

    Evidence for faithfulness is the reviewed reference facts, because capture.v1 stores
    row counts and query ids, not raw tool results.
    """
    if not live_enabled():
        raise JudgeDisabled(f"set {LIVE_FLAG}=1 to run the live judge")
    from deepeval.test_case import LLMTestCase, ToolCall

    metrics = build_metrics(judge_model)
    case = LLMTestCase(
        input=view.question or turn.question,
        actual_output=view.answer or "",
        retrieval_context=[f"{f.label} = {f.value}" for f in turn.reference.facts],
        tools_called=[
            ToolCall(name=t.name, input_parameters=t.arguments) for t in view.tools
        ],
        expected_tools=[
            ToolCall(name=t.name, input_parameters=t.arguments)
            for t in turn.expected_tools
        ],
    )
    out: dict[str, float] = {}
    for name, metric in metrics.items():
        out[name] = float(
            metric.measure(case, _show_indicator=False, _log_metric_to_confident=False)
        )
    return out
