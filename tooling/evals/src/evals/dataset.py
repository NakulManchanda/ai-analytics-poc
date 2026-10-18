"""Load, validate and hash the versioned golden dataset; refresh reference facts from SQL."""

from __future__ import annotations

import hashlib
from pathlib import Path

from evals.schema import GoldenCase

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATASET = PACKAGE_ROOT / "datasets/golden/v1/cases.jsonl"
REQUIRED_CATEGORIES = ("deterministic", "react", "multi_turn", "unsupported", "failure")


class DatasetError(ValueError):
    pass


def dataset_sha256(path: Path = DEFAULT_DATASET) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_cases(path: Path = DEFAULT_DATASET) -> list[GoldenCase]:
    cases = [
        GoldenCase.model_validate_json(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]
    validate_cases(cases)
    return cases


def validate_cases(cases: list[GoldenCase]) -> None:
    ids = [c.id for c in cases]
    if len(ids) != len(set(ids)):
        raise DatasetError("duplicate case ids")
    present = {c.category for c in cases}
    missing = [c for c in REQUIRED_CATEGORIES if c not in present]
    if missing:
        raise DatasetError(f"missing categories: {missing}")
    for case in cases:
        if case.category == "multi_turn" and len(case.turns) < 2:
            raise DatasetError(f"{case.id}: multi_turn needs >= 2 turns")
        for turn in case.turns:
            if turn.facts_without_sql():
                raise DatasetError(f"{case.id}: facts require reference.sql")
            if turn.behavior == "answer" and not turn.reference.facts:
                raise DatasetError(f"{case.id}: answer turns need reference facts")
            if turn.behavior != "answer" and not turn.must_mention_any:
                raise DatasetError(f"{case.id}: non-answer turns need must_mention_any")
            if turn.behavior == "failure" and turn.failure is None:
                raise DatasetError(f"{case.id}: failure turns need a failure spec")


def refresh_reference(cases: list[GoldenCase], parquet: Path, zones_csv: Path) -> int:
    """Recompute every fact value by running the hand-authored reference SQL (needs duckdb)."""
    import duckdb  # optional extra

    con = duckdb.connect()
    con.execute(f"CREATE VIEW trips AS SELECT * FROM read_parquet('{parquet}')")
    con.execute(f"CREATE VIEW zones AS SELECT * FROM read_csv_auto('{zones_csv}')")
    updated = 0
    for case in cases:
        for turn in case.turns:
            ref = turn.reference
            if not ref.sql:
                continue
            cur = con.execute(ref.sql)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            for fact in ref.facts:
                if fact.row >= len(rows):
                    raise DatasetError(
                        f"{case.id}/{fact.label}: SQL returned {len(rows)} rows"
                    )
                raw = rows[fact.row][cols.index(fact.col)]
                fact.value = float(raw) if fact.kind == "number" else str(raw)
                updated += 1
    return updated


def write_cases(cases: list[GoldenCase], path: Path = DEFAULT_DATASET) -> None:
    path.write_text(
        "".join(c.model_dump_json(exclude_none=False) + "\n" for c in cases)
    )
