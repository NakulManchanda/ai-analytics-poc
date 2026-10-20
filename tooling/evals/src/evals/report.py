"""Score a run directory and compare a candidate against a baseline."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path

from evals.dataset import DEFAULT_DATASET, load_cases
from evals.graders import TurnGrade, grade_turn
from evals.judge import METRIC_NAMES, RecordedJudge
from evals.manifest import read_manifest
from evals.schema import GoldenCase, RunManifest, Thresholds
from evals.traces import TurnView, load_turns

TRACES = "traces.jsonl"
JUDGE = "judge_scores.json"
MANIFEST = "manifest.json"


@dataclass
class RunScore:
    manifest: RunManifest
    grades: list[TurnGrade]
    views: dict[tuple[str, int], TurnView]
    judge: dict[str, float]  # mean per metric (empty when no recorded judge)
    by_category: dict[str, float] = field(default_factory=dict)
    diagnostic_ids: frozenset[str] = frozenset()

    @property
    def gated(self) -> list[TurnGrade]:
        """Grades that count toward aggregate quality gates (diagnostic cases excluded)."""
        return [g for g in self.grades if g.case_id not in self.diagnostic_ids]

    @property
    def diagnostics(self) -> list[TurnGrade]:
        return [g for g in self.grades if g.case_id in self.diagnostic_ids]

    @property
    def pass_rate(self) -> float:
        return sum(g.passed for g in self.gated) / len(self.gated)

    @property
    def tool_correctness(self) -> float:
        return statistics.fmean(g.tool_score for g in self.gated if not g.missing)

    @property
    def failure_rate(self) -> float:
        present = [v for v in self.views.values()]
        return sum(v.errored for v in present) / len(present) if present else 0.0

    @property
    def mean_steps(self) -> float:
        return statistics.fmean(v.steps for v in self.views.values())

    @property
    def total_cost(self) -> float | None:
        known = [v.cost_usd for v in self.views.values() if v.cost_usd is not None]
        return round(sum(known), 6) if known else None

    @property
    def total_tokens(self) -> int | None:
        known = [
            (v.input_tokens or 0) + (v.output_tokens or 0)
            for v in self.views.values()
            if v.input_tokens is not None or v.output_tokens is not None
        ]
        return sum(known) if known else None

    @property
    def p95_latency_ms(self) -> float | None:
        values = sorted(
            v.latency_ms for v in self.views.values() if v.latency_ms is not None
        )
        if not values:
            return None
        return values[min(len(values) - 1, int(round(0.95 * (len(values) - 1))))]

    @property
    def quality_qualified_goodput(self) -> float:
        """Share of turns that pass every deterministic check (quality-qualified, SLO-agnostic)."""
        return self.pass_rate

    def metrics(self) -> dict[str, float | None]:
        out: dict[str, float | None] = {
            "pass_rate": self.pass_rate,
            "tool_correctness_det": self.tool_correctness,
            "failure_rate": self.failure_rate,
            "mean_steps": self.mean_steps,
            "total_cost_usd": self.total_cost,
            "total_tokens": self.total_tokens,
            "p95_latency_ms": self.p95_latency_ms,
        }
        for name in METRIC_NAMES:
            out[f"judge_{name}"] = self.judge.get(name)
        return out


def score_run(run_dir: Path, cases: list[GoldenCase] | None = None) -> RunScore:
    cases = cases or load_cases(DEFAULT_DATASET)
    manifest = read_manifest(run_dir / MANIFEST)
    views = load_turns(run_dir / TRACES)
    grades = [
        grade_turn(c.id, i, turn, views.get((c.id, i)))
        for c in cases
        for i, turn in enumerate(c.turns)
    ]
    judge: dict[str, float] = {}
    judge_path = run_dir / JUDGE
    if judge_path.exists():
        recorded = RecordedJudge.load(judge_path)
        rows = [recorded.score(g.case_id, g.turn_index) for g in grades]
        rows = [r for r in rows if r]
        for name in METRIC_NAMES:
            vals = [r[name] for r in rows if name in r]
            if vals:
                judge[name] = statistics.fmean(vals)
    diagnostic = frozenset(c.id for c in cases if "diagnostic" in c.tags)
    score = RunScore(manifest, grades, views, judge, diagnostic_ids=diagnostic)
    cat = {c.id: c.category for c in cases}
    for category in sorted(set(cat.values())):
        gs = [g for g in score.gated if cat[g.case_id] == category]
        if not gs:
            continue
        score.by_category[category] = sum(g.passed for g in gs) / len(gs)
    return score


@dataclass
class Delta:
    metric: str
    baseline: float | None
    candidate: float | None
    delta: float | None
    regression: bool
    rule: str


def _pct(base: float, cand: float) -> float:
    return (cand - base) / base * 100 if base else 0.0


def compare(
    baseline: RunScore, candidate: RunScore, th: Thresholds | None = None
) -> list[Delta]:
    th = th or candidate.manifest.thresholds
    b, c = baseline.metrics(), candidate.metrics()
    out: list[Delta] = []

    def add(metric: str, regression_fn, rule: str) -> None:
        bv, cv = b.get(metric), c.get(metric)
        if bv is None or cv is None:
            out.append(
                Delta(
                    metric,
                    bv,
                    cv,
                    None,
                    False,
                    rule + " (not comparable: missing data)",
                )
            )
            return
        out.append(Delta(metric, bv, cv, cv - bv, bool(regression_fn(bv, cv)), rule))

    add(
        "pass_rate",
        lambda bv, cv: (
            cv < th.min_pass_rate_deterministic or bv - cv > th.max_pass_rate_drop
        ),
        f"candidate >= {th.min_pass_rate_deterministic} and drop <= {th.max_pass_rate_drop}",
    )
    add(
        "tool_correctness_det",
        lambda bv, cv: bv - cv > th.max_tool_correctness_drop,
        f"drop <= {th.max_tool_correctness_drop}",
    )
    add(
        "failure_rate",
        lambda bv, cv: cv - bv > th.max_failure_rate_increase,
        f"increase <= {th.max_failure_rate_increase}",
    )
    add(
        "mean_steps",
        lambda bv, cv: cv - bv > th.max_mean_steps_increase,
        f"increase <= {th.max_mean_steps_increase}",
    )
    add(
        "total_cost_usd",
        lambda bv, cv: _pct(bv, cv) > th.max_cost_increase_pct,
        f"increase <= {th.max_cost_increase_pct}%",
    )
    add(
        "p95_latency_ms",
        lambda bv, cv: _pct(bv, cv) > th.max_p95_latency_increase_pct,
        f"increase <= {th.max_p95_latency_increase_pct}%",
    )
    add("total_tokens", lambda bv, cv: False, "informational")
    for name in METRIC_NAMES:
        add(
            f"judge_{name}",
            lambda bv, cv: bv - cv > th.max_judge_score_drop,
            f"drop <= {th.max_judge_score_drop}",
        )
    return out


def regressions(deltas: list[Delta]) -> list[Delta]:
    return [d for d in deltas if d.regression]


def render_markdown(
    baseline: RunScore, candidate: RunScore, deltas: list[Delta]
) -> str:
    bm, cm = baseline.manifest, candidate.manifest
    lines = [
        f"# Eval delta: {cm.run_label} vs {bm.run_label}",
        "",
        f"- dataset: {cm.dataset_version} ({cm.dataset_sha256[:12]})"
        + (
            ""
            if bm.dataset_sha256 == cm.dataset_sha256
            else "  **WARNING: dataset differs from baseline**"
        ),
        f"- baseline: model `{bm.model_alias}`, prompt `{bm.prompt_version}`, judge `{bm.judge_model}` ({bm.judge_mode})",
        f"- candidate: model `{cm.model_alias}`, prompt `{cm.prompt_version}`, judge `{cm.judge_model}` ({cm.judge_mode})",
        "",
        "| metric | baseline | candidate | delta | status | rule |",
        "|---|---|---|---|---|---|",
    ]

    def fmt(v: float | None) -> str:
        return "n/a" if v is None else f"{v:.4g}"

    for d in deltas:
        status = "REGRESSION" if d.regression else "ok"
        lines.append(
            f"| {d.metric} | {fmt(d.baseline)} | {fmt(d.candidate)} | {fmt(d.delta)} | {status} | {d.rule} |"
        )

    def why(g: TurnGrade) -> str:
        if g.missing:
            return "missing trace"
        return ", ".join(k for k, v in g.checks.items() if not v)

    failing = [g for g in candidate.gated if not g.passed]
    lines += ["", f"## Candidate failures ({len(failing)})", ""]
    for g in failing:
        lines.append(f"- `{g.case_id}#{g.turn_index}`: {why(g)}")
    if candidate.diagnostics:
        lines += [
            "",
            "## Diagnostic cases (excluded from aggregate pass rate and gates)",
            "",
        ]
        for g in candidate.diagnostics:
            lines.append(
                f"- `{g.case_id}#{g.turn_index}`: "
                + ("pass" if g.passed else f"FAIL ({why(g)})")
            )
    lines += [
        "",
        "Judge scores are recorded verdicts, not ground truth; see docs/evals.md for limits.",
    ]
    return "\n".join(lines) + "\n"
