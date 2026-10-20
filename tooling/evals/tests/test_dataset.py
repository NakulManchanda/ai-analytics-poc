import json
import os
from collections import Counter
from pathlib import Path

import pytest

from evals.dataset import (
    DEFAULT_DATASET,
    DatasetError,
    dataset_sha256,
    load_cases,
    validate_cases,
)
from evals.schema import GoldenCase

DATA_DIR = Path(
    os.environ.get(
        "EVALS_DATA_DIR",
        Path(__file__).resolve().parents[3] / "data/nyc-yellow-taxi-2024-01",
    )
)


def test_dataset_size_and_categories():
    cases = load_cases()
    assert 30 <= len(cases) <= 40
    counts = Counter(c.category for c in cases)
    assert set(counts) == {
        "deterministic",
        "react",
        "multi_turn",
        "unsupported",
        "failure",
    }
    assert all(c.version == "v1" and c.source == "golden" for c in cases)


def test_every_answer_fact_has_reference_value_from_sql():
    for case in load_cases():
        for turn in case.turns:
            if turn.reference.facts:
                assert turn.reference.sql, case.id
            if turn.behavior == "answer":
                assert all(f.value is not None for f in turn.reference.facts), case.id


def test_multi_turn_has_follow_ups_and_unsupported_has_no_expected_tools():
    cases = {c.id: c for c in load_cases()}
    assert all(len(c.turns) >= 2 for c in cases.values() if c.category == "multi_turn")
    for c in cases.values():
        if c.category == "unsupported":
            assert all(not t.expected_tools for t in c.turns)


def test_duplicate_ids_rejected():
    cases = load_cases()
    with pytest.raises(DatasetError):
        validate_cases(cases + [cases[0]])


def test_missing_category_rejected():
    cases = [c for c in load_cases() if c.category != "failure"]
    with pytest.raises(DatasetError, match="failure"):
        validate_cases(cases)


def test_facts_without_sql_rejected():
    bad = json.loads(DEFAULT_DATASET.read_text().splitlines()[0])
    bad["turns"][0]["reference"]["sql"] = None
    cases = load_cases()
    cases[0] = GoldenCase.model_validate(bad)
    with pytest.raises(DatasetError, match="sql"):
        validate_cases(cases)


def test_dataset_hash_is_stable_hex():
    assert len(dataset_sha256()) == 64


@pytest.mark.skipif(
    not (DATA_DIR / "yellow_tripdata_2024-01.parquet").exists(),
    reason="pinned parquet not downloaded",
)
def test_reference_values_match_sql_on_pinned_parquet():
    """Drift guard: committed fact values must equal a fresh run of the reference SQL."""
    import copy

    from evals.dataset import refresh_reference

    cases = load_cases()
    fresh = copy.deepcopy(cases)
    refresh_reference(
        fresh,
        DATA_DIR / "yellow_tripdata_2024-01.parquet",
        DATA_DIR / "taxi_zone_lookup.csv",
    )
    assert [c.model_dump() for c in cases] == [c.model_dump() for c in fresh]


# --- #188 grader basis tags: tags/notes may change, golden values may not ---

_BASIS_TAGS = {
    "basis:legacy_unfiltered",
    "basis:governed_valid_records",
    "basis:mixed",
}
_NEW_TAGS = _BASIS_TAGS | {"basis-dependent", "diagnostic", "fault-injection"}


def _main_dataset_lines() -> list[dict]:
    import subprocess

    repo = Path(__file__).resolve().parents[3]
    try:
        out = subprocess.run(
            ["git", "show", "main:tooling/evals/datasets/golden/v1/cases.jsonl"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("main branch copy of the dataset is not available")
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def test_golden_values_unchanged_vs_main_only_tags_and_notes_differ():
    current = [json.loads(line) for line in DEFAULT_DATASET.read_text().splitlines() if line.strip()]
    main = _main_dataset_lines()
    assert [c["id"] for c in current] == [c["id"] for c in main]
    for cur, old in zip(current, main):
        assert len(cur["turns"]) == len(old["turns"]), cur["id"]
        for ct, ot in zip(cur["turns"], old["turns"]):
            for key in ("reference", "expected_tools", "question"):
                assert json.dumps(ct[key], sort_keys=True) == json.dumps(
                    ot[key], sort_keys=True
                ), (cur["id"], key)
            assert ct == ot, cur["id"]
        strip = lambda c: {k: v for k, v in c.items() if k not in ("tags", "notes")}
        assert strip(cur) == strip(old), cur["id"]
        assert set(old["tags"]) <= set(cur["tags"]), cur["id"]


def test_new_tag_vocabulary_and_one_basis_per_basis_dependent_case():
    for case in load_cases():
        new = {t for t in case.tags if t in _NEW_TAGS}
        bad = [t for t in case.tags if t.startswith("basis") and t not in _NEW_TAGS]
        assert not bad, (case.id, bad)
        basis = [t for t in case.tags if t in _BASIS_TAGS]
        if "basis-dependent" in new:
            assert len(basis) == 1, case.id
        else:
            assert not basis, case.id
