import importlib.util
from pathlib import Path

import pytest

from evals.dataset import load_cases
from evals.judge import (
    LIVE_FLAG,
    METRIC_NAMES,
    JudgeDisabled,
    RecordedJudge,
    build_metrics,
    live_score,
)
from evals.traces import TurnView

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def test_recorded_judge_replays_all_metrics_without_model():
    j = RecordedJudge.load(FIX / "baseline/judge_scores.json")
    s = j.score("det-004", 0)
    assert set(s) == set(METRIC_NAMES) and all(0 <= v <= 1 for v in s.values())
    assert j.score("nope", 0) is None


def test_live_judge_requires_opt_in(monkeypatch):
    monkeypatch.delenv(LIVE_FLAG, raising=False)
    with pytest.raises(JudgeDisabled):
        build_metrics("some-judge")
    case = load_cases()[0]
    with pytest.raises(JudgeDisabled):
        live_score(case.turns[0], TurnView("r", case.id, 0), "some-judge")


@pytest.mark.skipif(
    importlib.util.find_spec("deepeval") is None, reason="judge extra not installed"
)
def test_deepeval_metrics_construct_when_opted_in(monkeypatch):
    monkeypatch.setenv(LIVE_FLAG, "1")
    monkeypatch.setenv(
        "OPENAI_API_KEY", "dummy-not-used"
    )  # DeepEval wants a key at construction
    metrics = build_metrics("gpt-4o-mini")  # construction only; no model call is made
    assert set(metrics) == set(METRIC_NAMES)
