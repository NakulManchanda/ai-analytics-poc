import json
from pathlib import Path

from evals.report import compare, regressions, render_markdown, score_run
from evals.schema import Thresholds
from evals.synth import generate

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def _by_name(deltas):
    return {d.metric: d for d in deltas}


def test_baseline_scores_and_categories():
    base = score_run(FIX / "baseline")
    assert 0.9 <= base.pass_rate < 1.0
    assert set(base.by_category) == {
        "deterministic",
        "react",
        "multi_turn",
        "unsupported",
        "failure",
    }
    assert base.manifest.dataset_version == "v1"


def test_acceptable_candidate_has_no_regressions_and_is_cheaper():
    base, cand = score_run(FIX / "baseline"), score_run(FIX / "candidate")
    deltas = compare(base, cand)
    assert regressions(deltas) == []
    assert _by_name(deltas)["total_cost_usd"].delta < 0


def test_regressed_candidate_trips_thresholds():
    base, bad = score_run(FIX / "baseline"), score_run(FIX / "regressed")
    names = {d.metric for d in regressions(compare(base, bad))}
    assert {
        "pass_rate",
        "tool_correctness_det",
        "p95_latency_ms",
        "judge_answer_quality",
    } <= names


def test_custom_threshold_can_make_candidate_fail():
    base, cand = score_run(FIX / "baseline"), score_run(FIX / "candidate")
    strict = Thresholds(max_mean_steps_increase=0.0)
    assert "mean_steps" in {d.metric for d in regressions(compare(base, cand, strict))}


def test_missing_trace_counts_as_failure(tmp_path):
    run = tmp_path / "r"
    run.mkdir()
    for name in ("manifest.json", "judge_scores.json"):
        (run / name).write_text((FIX / "baseline" / name).read_text())
    lines = (FIX / "baseline/traces.jsonl").read_text().splitlines()
    (run / "traces.jsonl").write_text(
        "\n".join(ln for ln in lines if '"det-004"' not in ln) + "\n"
    )
    score = score_run(run)
    assert any(g.missing and g.case_id == "det-004" for g in score.grades)
    assert score.pass_rate < score_run(FIX / "baseline").pass_rate


def test_markdown_report_lists_regressions_and_manifest():
    base, bad = score_run(FIX / "baseline"), score_run(FIX / "regressed")
    md = render_markdown(base, bad, compare(base, bad))
    assert (
        "REGRESSION" in md
        and "answer-tiny" in md
        and "prompt-v2" in md
        and "not ground truth" in md
    )


def test_committed_fixtures_match_generator(tmp_path):
    generate(tmp_path)
    for label in ("baseline", "candidate", "regressed"):
        for name in ("traces.jsonl", "manifest.json", "judge_scores.json"):
            assert (tmp_path / label / name).read_text() == (
                FIX / label / name
            ).read_text(), (label, name)


def test_cost_unknown_is_not_reported_as_zero():
    base = score_run(FIX / "baseline")
    assert base.views[("fail-004", 0)].cost_usd is None
    assert (
        json.loads((FIX / "baseline/manifest.json").read_text())["judge_mode"]
        == "recorded"
    )


def test_diagnostic_cases_are_graded_but_excluded_from_aggregates():
    score = score_run(FIX / "baseline")
    assert score.diagnostic_ids == {"multi-001"}
    assert any(g.case_id == "multi-001" for g in score.grades)
    assert all(g.case_id != "multi-001" for g in score.gated)
    md = render_markdown(score, score, compare(score, score))
    assert "Diagnostic cases" in md and "multi-001#1" in md
