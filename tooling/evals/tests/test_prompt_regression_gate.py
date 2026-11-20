"""#179: offline deterministic CI prompt-regression gate (zero paid model calls).

1. Runs the real `evals.report.compare()` delta gate over recorded fixtures
   (`fixtures/baseline` vs `fixtures/candidate` and `fixtures/regressed`), proving CI
   deterministically rejects tool/answer regressions without calling Bedrock or LiteLLM.
2. Dynamically checks every holdout turn question from `datasets/golden/v1/splits.json`
   against `services/app/app/prompts/bundles.py` to prevent holdout leakage into prompts.
"""

from __future__ import annotations

import re
from pathlib import Path

from evals.dataset import load_cases, load_holdout_cases, load_splits
from evals.report import compare, regressions, score_run

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = PACKAGE_ROOT / "fixtures"
PROMPT_BUNDLES = REPO_ROOT / "services/app/app/prompts/bundles.py"


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def test_recorded_fixture_ci_gate_rejects_regressed_run_offline() -> None:
    """Recorded-fixture behavioral gate: candidate passes, regressed fails offline."""
    cases = load_cases()
    base = score_run(FIXTURES / "baseline", cases)
    cand = score_run(FIXTURES / "candidate", cases)
    reg = score_run(FIXTURES / "regressed", cases)

    cand_regs = regressions(compare(base, cand))
    assert not cand_regs, f"candidate fixture unexpectedly regressed: {cand_regs}"

    reg_regs = regressions(compare(base, reg))
    assert reg_regs, "regressed fixture must fail the deterministic CI gate"


def test_no_holdout_turn_questions_leak_into_prompt_bundles() -> None:
    """Programmatic disjointness check: no holdout question appears in app prompt bundles or golden/v1."""
    v1_questions = {_normalize(t.question) for c in load_cases() for t in c.turns}
    holdout_cases = {c.id: c for c in load_holdout_cases()}
    splits = load_splits()
    bundles_text = _normalize(PROMPT_BUNDLES.read_text())

    leaked: list[tuple[str, str]] = []
    overlapped: list[tuple[str, str]] = []
    for cid in splits["holdout"]:
        for turn in holdout_cases[cid].turns:
            q_norm = _normalize(turn.question)
            if len(q_norm) >= 12 and q_norm in bundles_text:
                leaked.append((cid, turn.question))
            if q_norm in v1_questions:
                overlapped.append((cid, turn.question))
    assert not leaked, f"holdout questions leaked into prompt bundles: {leaked}"
    assert not overlapped, f"holdout questions overlap golden/v1: {overlapped}"
