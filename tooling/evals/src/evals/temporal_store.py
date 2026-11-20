"""Crash-safe submission journal and reconcilable case execution for the Temporal eval pilot.

Owns file-backed submission tracking (``submissions.jsonl``) and locked updates to standard eval
run-folder artifacts (``submitted.jsonl``, ``results.jsonl``, ``matches.json``, ``events/<run_id>.txt``,
``state.json``) so outputs stay 100% compatible with ``evals run-freeze``, ``run-grade``,
``run-reconcile``, ``run-trace-check``, and ``run-review``.

Boundary / invariant:
- Never blindly retries ``POST /api/runs`` after an ambiguous submission window.
- Never modifies or depends on ``services/app`` internals; optional server-side lookup
  (``GET /api/runs/by-submission/<key>``) degrades cleanly on 404/405/501.
- Never imports ``temporalio`` so it can be tested and used independently of Temporal.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from urllib.parse import quote

from evals.dataset import DEFAULT_DATASET
from evals.live_run import (
    FAILED_TERMINALS,
    TERMINAL,
    _append_row,
    _evidence,
    _now,
    _read_rows,
    _write_matches,
    read_events,
)
from evals.runs import RunError, read_state, write_json_atomic

SUBMISSION_KEY_HEADER = "x-eval-submission-key"
AMBIGUOUS_TERMINAL = "ambiguous_submission"


def submission_key(eval_run_id: str, case_id: str, turn_index: int) -> str:
    """Deterministic key tying one eval run, case, and turn to at most one app submission."""
    return f"{eval_run_id}:{case_id}:t{turn_index}"


@contextlib.contextmanager
def run_dir_lock(run_dir: Path) -> Iterator[None]:
    """Exclusive advisory lock protecting shared run-folder JSON/JSONL writes."""
    lock_path = run_dir / ".evals.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def load_cases_raw(cases_path: Path | None = None) -> list[dict]:
    path = cases_path or DEFAULT_DATASET
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def resolve_batch_plan(
    run_dir: Path,
    web: str | None,
    case_ids: list[str],
    all_cases: bool,
    cases_path: Path | None = None,
) -> tuple[str, list[str], list[tuple[str, int]], Path]:
    """Validate manifest + requested cases; return (web_url, wanted_case_ids, expected_turns, cases_path)."""
    manifest_p = run_dir / "manifest.json"
    if not manifest_p.is_file():
        raise RunError(f"{manifest_p} missing; run `evals run-start` first")
    manifest = json.loads(manifest_p.read_text())
    resolved_path = cases_path or DEFAULT_DATASET
    cases = load_cases_raw(resolved_path)
    by_id = {c["id"]: c for c in cases}
    resolved_web = web or manifest.get("web") or "http://localhost:3100"
    wanted = (
        [c["id"] for c in cases]
        if all_cases
        else (case_ids or manifest.get("cases") or [])
    )
    unknown = [c for c in wanted if c not in by_id]
    if unknown or not wanted:
        raise RunError(f"unknown or empty case list: {unknown}")
    scope = manifest.get("cases") or list(wanted)
    for cid in wanted:
        if cid not in scope:
            scope = [*scope, cid]
    expected = [(c, i) for c in scope for i in range(len(by_id[c]["turns"]))]
    return resolved_web, list(wanted), expected, resolved_path


def load_submission_journal(run_dir: Path) -> dict[str, dict]:
    """Return effective submission state per ``submission_key`` from ``submissions.jsonl``.

    Precedence per key: ``accepted`` (has a concrete ``run_id``) > ``ambiguous_submission`` > ``submitting``.
    """
    state: dict[str, dict] = {}
    for row in _read_rows(run_dir / "submissions.jsonl"):
        key = row.get("submission_key")
        if not key:
            continue
        cur = state.get(key)
        status = row.get("status")
        if status == "accepted" and row.get("run_id"):
            state[key] = row
        elif status == "ambiguous_submission":
            if cur is None or cur.get("status") != "accepted":
                state[key] = row
        elif status == "submitting" and cur is None:
            state[key] = row
    return state


def post_run_with_key(
    web: str,
    prompt: str,
    conversation_id: str | None,
    eval_run_id: str | None,
    sub_key: str,
    timeout_s: int = 30,
) -> dict:
    """POST ``/api/runs`` carrying ``x-eval-submission-key`` (safely ignored by unmodified FastAPI)."""
    body: dict = {"prompt": prompt}
    if conversation_id:
        body["conversation_id"] = conversation_id
    if eval_run_id:
        body["eval_run_id"] = eval_run_id
    req = urllib.request.Request(
        f"{web}/api/runs",
        data=json.dumps(body).encode(),
        headers={
            "content-type": "application/json",
            SUBMISSION_KEY_HEADER: sub_key,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as r:
        return json.load(r)


def lookup_submission(
    web: str, sub_key: str, timeout_s: int = 15
) -> tuple[str, str] | None:
    """Query optional ``GET /api/runs/by-submission/{sub_key}`` to resolve a blind-window crash.

    Returns ``(run_id, conversation_id)`` on HTTP 200 JSON, and ``None`` cleanly on 404/405/501
    (the expected behavior for an unmodified ``services/app``). Raises on transient transport errors
    so recovery can retry lookup without ever blindly retrying ``POST /api/runs``.
    """
    url = f"{web}/api/runs/by-submission/{quote(sub_key, safe='')}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as r:
            if r.status != 200:
                return None
            data = json.load(r)
    except urllib.error.HTTPError as err:
        if err.code in (404, 405, 501):
            return None
        raise
    if isinstance(data, dict) and data.get("run_id") and data.get("conversation_id"):
        return str(data["run_id"]), str(data["conversation_id"])
    return None


def update_run_state(
    run_dir: Path,
    expected_turns: list[tuple[str, int]],
    status: str = "running",
) -> dict:
    """Synchronize ``matches.json`` and ``state.json`` with ``results.jsonl`` under lock."""
    with run_dir_lock(run_dir):
        rows = _read_rows(run_dir / "results.jsonl")
        old_state = read_state(run_dir)
        started = (old_state or {}).get("started_at") or _now()
        recorded = {(r["case_id"], r["turn_index"]): r for r in rows}
        inc = [
            {
                "case_id": c,
                "turn_index": i,
                "terminal": recorded[(c, i)]["terminal"],
            }
            for c, i in expected_turns
            if (c, i) in recorded and _evidence(recorded[(c, i)]) != "complete"
        ]
        missing = [
            {"case_id": c, "turn_index": i}
            for c, i in expected_turns
            if (c, i) not in recorded
        ]
        final_status = (
            "running"
            if status == "running"
            else ("incomplete" if inc or missing else "complete")
        )
        state = {
            "status": final_status,
            "started_at": started,
            "finished_at": None if final_status == "running" else _now(),
            "turns_total": len(expected_turns),
            "turns_recorded": len(recorded),
            "incomplete_turns": inc,
            "missing_turns": missing,
        }
        _write_matches(run_dir, rows)
        write_json_atomic(run_dir / "state.json", state)
        return state


def _record_submitted_once(
    run_dir: Path,
    run_ref: str,
    case_id: str,
    turn_index: int,
    conversation_id: str,
) -> None:
    submitted_p = run_dir / "submitted.jsonl"
    existing = {r.get("run_id") for r in _read_rows(submitted_p)}
    if run_ref in existing:
        return
    _append_row(
        submitted_p,
        {
            "run_id": run_ref,
            "case_id": case_id,
            "turn_index": turn_index,
            "conversation_id": conversation_id,
            "ts": _now(),
        },
    )


def _record_result_row(
    run_dir: Path,
    row: dict,
    expected_turns: list[tuple[str, int]],
) -> None:
    results_p = run_dir / "results.jsonl"
    with run_dir_lock(run_dir):
        existing = {(r["case_id"], r["turn_index"]) for r in _read_rows(results_p)}
        if (row["case_id"], row["turn_index"]) not in existing:
            _append_row(results_p, row)
    update_run_state(run_dir, expected_turns, status="running")


def _write_events_atomic(run_dir: Path, run_ref: str, raw: str) -> None:
    events_dir = run_dir / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    target = events_dir / f"{run_ref}.txt"
    tmp = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    tmp.write_text(raw)
    os.replace(tmp, target)


def execute_case_reconcilable(
    run_dir: Path,
    eval_run_id: str,
    web: str,
    case: dict,
    expected_turns: list[tuple[str, int]],
    timeout_s: int = 120,
    heartbeat: Callable[[str], None] | None = None,
) -> dict:
    """Execute or reconcile one whole multi-turn case without rerunning completed turns.

    Recovery rules per turn:
    1. Already recorded in ``results.jsonl`` with complete evidence -> reuse ``conversation_id`` and skip.
    2. Already recorded in ``results.jsonl`` with incomplete evidence -> do not duplicate; stop case.
    3. ``accepted`` in ``submissions.jsonl`` -> skip ``POST /api/runs``; reconcile via ``GET /api/runs/{id}/events``.
    4. ``submitting`` in ``submissions.jsonl`` (blind-window crash) -> try ``lookup_submission``:
       - resolved -> record ``accepted`` + ``submitted.jsonl`` and reconcile via SSE;
       - unresolved (404/405/501) -> record ``ambiguous_submission`` (never blindly retry POST) and stop case.
    5. No journal record -> fsync ``submitting`` -> ``POST /api/runs`` -> fsync ``accepted`` + ``submitted.jsonl``
       -> stream SSE -> persist ``events/<run_id>.txt`` + ``results.jsonl`` + ``matches.json`` + ``state.json``.
    """
    case_id = case["id"]
    turns = case["turns"]
    (run_dir / "events").mkdir(parents=True, exist_ok=True)

    with run_dir_lock(run_dir):
        existing_rows = {
            r["turn_index"]: r
            for r in _read_rows(run_dir / "results.jsonl")
            if r.get("case_id") == case_id
        }
        journal = load_submission_journal(run_dir)

    # Fast path: whole case already completed on an earlier attempt.
    if len(existing_rows) == len(turns) and all(
        _evidence(existing_rows[i]) == "complete" for i in range(len(turns))
    ):
        return {
            "case_id": case_id,
            "status": "complete",
            "skipped_already_complete": True,
            "reconciled_turns": 0,
            "ambiguous_turns": 0,
            "turns_completed": len(turns),
        }

    conv: str | None = None
    prev_failed = False
    reconciled_turns = 0
    ambiguous_turns = 0
    turns_completed = 0

    for idx, turn in enumerate(turns):
        if heartbeat:
            heartbeat(f"{case_id}:t{idx}:start")

        if idx in existing_rows:
            prev_row = existing_rows[idx]
            if _evidence(prev_row) == "complete":
                conv = prev_row.get("conversation_id")
                prev_failed = prev_row.get("terminal") in FAILED_TERMINALS
                turns_completed += 1
                continue
            if prev_row.get("terminal") == AMBIGUOUS_TERMINAL:
                ambiguous_turns += 1
            return {
                "case_id": case_id,
                "status": "incomplete",
                "skipped_already_complete": False,
                "reconciled_turns": reconciled_turns,
                "ambiguous_turns": ambiguous_turns,
                "turns_completed": turns_completed,
            }

        t0 = time.monotonic()
        sub_key = submission_key(eval_run_id, case_id, idx)
        entry = journal.get(sub_key)
        row: dict = {
            "case_id": case_id,
            "turn_index": idx,
            "question": turn["question"],
            "conversation_id": conv,
        }
        run_ref: str | None = None
        terminal: str | None = None

        if entry is not None and entry.get("status") == "accepted":
            # Post-accept recovery: never call POST /api/runs again; replay SSE for existing run_id.
            run_ref = str(entry["run_id"])
            conv = str(entry.get("conversation_id") or conv or "")
            reconciled_turns += 1
            with run_dir_lock(run_dir):
                _record_submitted_once(run_dir, run_ref, case_id, idx, conv)
        elif entry is not None and entry.get("status") == AMBIGUOUS_TERMINAL:
            row.update(run_id=None, terminal=AMBIGUOUS_TERMINAL, evidence="incomplete")
            row["wall_s"] = round(time.monotonic() - t0, 2)
            _record_result_row(run_dir, row, expected_turns)
            ambiguous_turns += 1
            return {
                "case_id": case_id,
                "status": "incomplete",
                "skipped_already_complete": False,
                "reconciled_turns": reconciled_turns,
                "ambiguous_turns": ambiguous_turns,
                "turns_completed": turns_completed,
            }
        elif entry is not None and entry.get("status") == "submitting":
            # Blind-window recovery: check optional server lookup; refuse blind POST retry if absent.
            resolved = lookup_submission(web, sub_key)
            if resolved is not None:
                run_ref, conv = resolved
                reconciled_turns += 1
                with run_dir_lock(run_dir):
                    _append_row(
                        run_dir / "submissions.jsonl",
                        {
                            "submission_key": sub_key,
                            "case_id": case_id,
                            "turn_index": idx,
                            "run_id": run_ref,
                            "conversation_id": conv,
                            "status": "accepted",
                            "source": "lookup",
                            "ts": _now(),
                        },
                    )
                    _record_submitted_once(run_dir, run_ref, case_id, idx, conv)
            else:
                with run_dir_lock(run_dir):
                    _append_row(
                        run_dir / "submissions.jsonl",
                        {
                            "submission_key": sub_key,
                            "case_id": case_id,
                            "turn_index": idx,
                            "conversation_id": conv,
                            "status": AMBIGUOUS_TERMINAL,
                            "ts": _now(),
                        },
                    )
                row.update(
                    run_id=None,
                    terminal=AMBIGUOUS_TERMINAL,
                    evidence="incomplete",
                )
                if prev_failed:
                    row["after_failed_turn"] = True
                row["wall_s"] = round(time.monotonic() - t0, 2)
                _record_result_row(run_dir, row, expected_turns)
                ambiguous_turns += 1
                return {
                    "case_id": case_id,
                    "status": "incomplete",
                    "skipped_already_complete": False,
                    "reconciled_turns": reconciled_turns,
                    "ambiguous_turns": ambiguous_turns,
                    "turns_completed": turns_completed,
                }
        else:
            # Fresh submission: fsync pre-submit intent BEFORE network POST.
            with run_dir_lock(run_dir):
                _append_row(
                    run_dir / "submissions.jsonl",
                    {
                        "submission_key": sub_key,
                        "case_id": case_id,
                        "turn_index": idx,
                        "conversation_id": conv,
                        "status": "submitting",
                        "ts": _now(),
                    },
                )
            try:
                acc = post_run_with_key(
                    web, turn["question"], conv, eval_run_id, sub_key
                )
                conv = str(acc["conversation_id"])
                run_ref = str(acc["run_id"])
            except (OSError, ValueError, KeyError) as exc:
                row.update(
                    run_id=None,
                    terminal=f"submit_error: {exc!r}",
                    evidence="incomplete",
                )
                if prev_failed:
                    row["after_failed_turn"] = True
                row["wall_s"] = round(time.monotonic() - t0, 2)
                _record_result_row(run_dir, row, expected_turns)
                return {
                    "case_id": case_id,
                    "status": "incomplete",
                    "skipped_already_complete": False,
                    "reconciled_turns": reconciled_turns,
                    "ambiguous_turns": ambiguous_turns,
                    "turns_completed": turns_completed,
                }
            else:
                with run_dir_lock(run_dir):
                    _append_row(
                        run_dir / "submissions.jsonl",
                        {
                            "submission_key": sub_key,
                            "case_id": case_id,
                            "turn_index": idx,
                            "run_id": run_ref,
                            "conversation_id": conv,
                            "status": "accepted",
                            "source": "post",
                            "ts": _now(),
                        },
                    )
                    _record_submitted_once(run_dir, run_ref, case_id, idx, conv)

        if heartbeat:
            heartbeat(f"{case_id}:t{idx}:events:{run_ref}")
        raw, terminal = read_events(web, run_ref, timeout_s)
        _write_events_atomic(run_dir, run_ref, raw)
        row.update(conversation_id=conv, run_id=run_ref, terminal=terminal)
        row["evidence"] = "complete" if terminal in TERMINAL else "incomplete"
        if prev_failed:
            row["after_failed_turn"] = True
        prev_failed = terminal in FAILED_TERMINALS
        row["wall_s"] = round(time.monotonic() - t0, 2)
        _record_result_row(run_dir, row, expected_turns)

        if row["evidence"] != "complete":
            return {
                "case_id": case_id,
                "status": "incomplete",
                "skipped_already_complete": False,
                "reconciled_turns": reconciled_turns,
                "ambiguous_turns": ambiguous_turns,
                "turns_completed": turns_completed,
            }
        turns_completed += 1

    return {
        "case_id": case_id,
        "status": "complete",
        "skipped_already_complete": False,
        "reconciled_turns": reconciled_turns,
        "ambiguous_turns": ambiguous_turns,
        "turns_completed": turns_completed,
    }
