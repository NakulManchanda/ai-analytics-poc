"""Generate synthetic capture.v1 fixtures from the golden cases (deterministic, no model).

These are NOT observations of a real system. They exist so normal CI exercises ingest, graders,
reports and regression thresholds without a paid model. Real traces arrive via the capture
track (#169) and are ingested by the same code path.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from evals.dataset import load_cases
from evals.graders import grade_turn
from evals.manifest import build_manifest, write_manifest
from evals.schema import GoldenCase, Thresholds, Turn

PRICING_VERSION = "synthetic-v0"


@dataclass
class Profile:
    label: str
    model_alias: str
    prompt_version: str
    in_tokens: int
    out_tokens: int
    usd_per_mtok_in: float
    usd_per_mtok_out: float
    latency_ms: float
    wrong_number: set[str] = field(default_factory=set)
    extra_steps: set[str] = field(default_factory=set)
    bad_args: set[str] = field(default_factory=set)
    fabricate: set[str] = field(default_factory=set)
    judge_bias: float = 0.0


PROFILES = {
    "baseline": Profile(
        "baseline",
        "answer-large",
        "prompt-v1",
        2400,
        220,
        3.0,
        15.0,
        2400,
        wrong_number={"react-008"},
        bad_args={"multi-004"},
    ),
    "candidate": Profile(
        "candidate",
        "answer-small",
        "prompt-v1",
        2300,
        200,
        0.8,
        4.0,
        1900,
        wrong_number={"react-008"},
        extra_steps={"react-004"},
        bad_args={"multi-004"},
        judge_bias=-0.01,
    ),
    "regressed": Profile(
        "regressed",
        "answer-tiny",
        "prompt-v2",
        2600,
        260,
        0.25,
        1.25,
        3600,
        wrong_number={
            "react-008",
            "det-008",
            "det-005",
            "react-002",
            "multi-003",
            "det-013",
        },
        extra_steps={"react-004", "react-001", "react-006", "react-007"},
        bad_args={"multi-004", "det-010", "react-005", "det-012"},
        fabricate={"uns-001", "uns-004", "fail-002"},
        judge_bias=-0.15,
    ),
}


def _fmt(value: float) -> str:
    return f"{int(value):,}" if value == int(value) else f"{value:,}"


def _jitter(*parts: str) -> float:
    h = hashlib.sha256("|".join(parts).encode()).digest()
    return h[0] / 255.0


def _answer(turn: Turn, case_id: str, p: Profile) -> str:
    if turn.behavior == "unsupported":
        base = "I cannot determine that: the dataset does not contain this information."
        return base + (
            " Roughly 120,000 trips were affected." if case_id in p.fabricate else ""
        )
    if turn.behavior == "failure":
        kind = turn.failure.kind if turn.failure else ""
        if kind == "empty_result":
            return "The query ran but there are no trips for that zone (0 trips), so there is nothing to break down."
        text = "I could not complete that: the request failed with an error. Please try again."
        return text + (
            " Previously about 4,500 trips were reported."
            if case_id in p.fabricate
            else ""
        )
    parts = []
    corrupted = False
    for fact in turn.reference.facts:
        value = fact.value
        if fact.kind == "number" and case_id in p.wrong_number and not corrupted:
            value = float(value) * 1.5  # type: ignore[arg-type]
            corrupted = True
        parts.append(
            f"{fact.label.replace('_', ' ')}: {_fmt(float(value)) if fact.kind == 'number' else value}"
        )  # type: ignore[arg-type]
    return "Based on the governed query, " + "; ".join(parts) + "."


def _records(case: GoldenCase, p: Profile) -> list[dict]:
    recs: list[dict] = []
    run_id = f"{p.label}-{case.id}"
    step = 0
    for ti, turn in enumerate(case.turns):
        base = dict(
            schema_version="capture.v1",
            run_id=run_id,
            turn_id=f"t{ti}",
            tenant_id="synthetic",
            trace_id=hashlib.sha256(run_id.encode()).hexdigest()[:32],
            case_id=case.id,
            turn_index=ti,
        )

        def add(rec: dict, base: dict = base) -> None:
            nonlocal step
            rec.update(base, step=step, call_id=f"{run_id}-{step}")
            recs.append(rec)
            step += 1

        provider_error = turn.failure and turn.failure.kind == "model_provider_error"
        j = _jitter(p.label, case.id, str(ti))
        in_tok = int(p.in_tokens * (0.8 + 0.4 * j))
        out_tok = int(p.out_tokens * (0.8 + 0.4 * j))
        model = dict(
            record_type="model_call",
            provider_model_id=f"synthetic/{p.model_alias}",
            model_alias=p.model_alias,
            prompt_template_version=p.prompt_version,
            fallback_used=False,
            cost_kind="estimated",
            pricing_version=PRICING_VERSION,
        )

        def cost(i: int, o: int) -> float:
            return round(i * p.usd_per_mtok_in / 1e6 + o * p.usd_per_mtok_out / 1e6, 6)

        if (
            turn.expected_tools
            or turn.failure
            and turn.failure.kind != "model_provider_error"
        ):
            add(
                dict(
                    model,
                    role="planner",
                    input_tokens=in_tok // 2,
                    output_tokens=out_tok // 4,
                    latency_ms=round(p.latency_ms * 0.3 * (0.8 + 0.4 * j), 1),
                    finish_reason="tool_use",
                    cost_usd=cost(in_tok // 2, out_tok // 4),
                    question=turn.question,
                )
            )
        tools = list(turn.expected_tools)
        if case.id in p.bad_args and tools:
            first = tools[0]
            args = dict(first.arguments)
            key = next(iter(args), None)
            if key:
                args[key] = (
                    ["__wrong__"] if isinstance(args[key], list) else "__wrong__"
                )
            tools[0] = first.model_copy(update={"arguments": args})
        if case.id in p.extra_steps and tools:
            tools = tools + [tools[-1]] * (turn.max_steps - len(tools) + 1)
        failure = turn.failure
        for n, t in enumerate(tools):
            status = "ok"
            if failure and failure.tool_status and n == 0:
                status = failure.tool_status
            add(
                dict(
                    record_type="tool_call",
                    tool_name=t.name,
                    tool_call_id=f"tc-{run_id}-{step}",
                    arguments=t.arguments,
                    query_id=f"q-{run_id}-{step}" if status == "ok" else None,
                    status=status,
                    row_count=0 if (failure and failure.kind == "empty_result") else 20,
                )
            )
        if provider_error:
            add(
                dict(
                    model,
                    role="answer",
                    error="provider_unavailable",
                    finish_reason="error",
                    input_tokens=None,
                    output_tokens=None,
                    latency_ms=round(p.latency_ms * 0.2, 1),
                    cost_usd=None,
                    cost_kind="unknown",
                    answer=_answer(turn, case.id, p),
                    question=turn.question,
                )
            )
        else:
            add(
                dict(
                    model,
                    role="answer",
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    latency_ms=round(p.latency_ms * (0.8 + 0.4 * j), 1),
                    ttft_ms=round(p.latency_ms * 0.15, 1),
                    finish_reason="stop",
                    cost_usd=cost(in_tok, out_tok),
                    question=turn.question,
                    answer=_answer(turn, case.id, p),
                )
            )
    return recs


def generate(out_root: Path, dataset: Path | None = None) -> None:
    cases = load_cases(dataset) if dataset else load_cases()
    for label, p in PROFILES.items():
        run_dir = out_root / label
        run_dir.mkdir(parents=True, exist_ok=True)
        records = [r for c in cases for r in _records(c, p)]
        (run_dir / "traces.jsonl").write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in records)
        )
        manifest = build_manifest(
            run_label=p.label,
            model_alias=p.model_alias,
            prompt_version=p.prompt_version,
            judge_model="synthetic-recorded-judge",
            thresholds=Thresholds(),
        )
        write_manifest(manifest, run_dir / "manifest.json")
        _write_judge(run_dir, cases, p)


def _write_judge(run_dir: Path, cases: list[GoldenCase], p: Profile) -> None:
    from evals.judge import METRIC_NAMES, turn_key
    from evals.traces import load_turns

    views = load_turns(run_dir / "traces.jsonl")
    scores: dict[str, dict[str, float]] = {}
    for c in cases:
        for i, turn in enumerate(c.turns):
            g = grade_turn(c.id, i, turn, views.get((c.id, i)))
            base = 0.92 if g.passed else 0.35
            scores[turn_key(c.id, i)] = {
                m: round(
                    min(
                        1.0,
                        max(
                            0.0,
                            base
                            + p.judge_bias
                            + (_jitter(p.label, c.id, str(i), m) - 0.5) * 0.06,
                        ),
                    ),
                    3,
                )
                for m in METRIC_NAMES
            }
    (run_dir / "judge_scores.json").write_text(
        json.dumps(
            {
                "judge_model": "synthetic-recorded-judge",
                "synthetic": True,
                "scores": scores,
            },
            indent=1,
            sort_keys=True,
        )
        + "\n"
    )
