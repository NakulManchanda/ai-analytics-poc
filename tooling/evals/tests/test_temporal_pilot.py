"""Opt-in Temporal eval workflow pilot unit tests and real OS-process SIGKILL crash-recovery proofs (#253).

All proofs run against a local stdlib fixture HTTP server and Temporal's local test server
(``WorkflowEnvironment.start_local()``) with real child worker processes killed via ``SIGKILL``.
Zero paid Bedrock/LiteLLM calls and zero Docker dependencies.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

import pytest
from temporalio.testing import WorkflowEnvironment

from evals.cli import main
from evals.runs import load_run_reports
from evals.temporal_pilot import EvalBatchWorkflow, run_temporal_eval
from evals.temporal_store import (
    AMBIGUOUS_TERMINAL,
    SUBMISSION_KEY_HEADER,
    execute_case_reconcilable,
    load_cases_raw,
    load_submission_journal,
    resolve_batch_plan,
    submission_key,
)

PILOT_CASES = ["det-001", "multi-001", "det-004"]


def _capture_fixture_for_question(question: str) -> tuple[str, dict, str, dict]:
    """Return realistic (tool_name, tool_args, answer, tool_output_payload) for the 3 pilot cases."""
    if "date range, row count" in question:
        return (
            "describe_taxi_dataset",
            {"include_column_stats": False},
            "The dataset covers January 2024 with 2,964,621 rows.",
            {
                "query_id": "q-det-001",
                "columns": ["row_count", "max_pickup_date"],
                "rows": [[2964621, "2024-01-31"]],
                "row_count": 1,
                "truncated": False,
            },
        )
    if "Compare that with the second highest zone" in question:
        return (
            "compare_taxi_segments",
            {
                "segment_dimension": "pickup_zone",
                "measures": ["trip_count"],
                "baseline_filters": {"pickup_zone": "JFK Airport"},
                "comparison_filters": {"pickup_zone": "Midtown Center"},
            },
            "Midtown Center is the second highest zone compared with JFK Airport.",
            {
                "query_id": "q-multi-001-t1",
                "columns": ["zone", "trips"],
                "rows": [["Midtown Center", 141740], ["JFK Airport", 141182]],
                "row_count": 2,
                "truncated": False,
            },
        )
    return (
        "query_taxi_data",
        {"analysis": "top_pickup_zones", "limit": 5},
        "JFK Airport has the most pickups with 145,240 trips.",
        {
            "query_id": "q-top-zones",
            "columns": ["zone", "trips"],
            "rows": [["JFK Airport", 145240], ["Midtown Center", 143471]],
            "row_count": 2,
            "truncated": False,
        },
    )


class _FixtureEvalServer:
    """Local HTTP server simulating FastAPI /api/runs, replayable SSE, capture.v1, and optional lookup."""

    def __init__(
        self,
        capture_dir: Path,
        *,
        lookup_enabled: bool = False,
        hold_post_for_key: str | None = None,
        hold_events_for_key: str | None = None,
    ) -> None:
        self.capture_dir = capture_dir
        self.lookup_enabled = lookup_enabled
        self.hold_post_for_key = hold_post_for_key
        self.hold_events_for_key = hold_events_for_key

        self.lock = threading.Lock()
        self.seq = 0
        self.posts_by_key: dict[str, int] = {}
        self.total_posts = 0
        self.by_submission_key: dict[str, tuple[str, str]] = {}
        self.run_to_key: dict[str, str] = {}
        self.run_to_payload: dict[str, dict] = {}
        self.events_get_count: dict[str, int] = {}
        self.lookup_requests: list[str] = []

        self.crash_ready = threading.Event()
        self.release_hold = threading.Event()
        self._held_post_once = False
        self._held_events_once = False

        server_ref = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_POST(self):
                if self.path != "/api/runs":
                    self.send_response(404)
                    self.send_header("content-length", "0")
                    self.end_headers()
                    return
                length = int(self.headers.get("content-length", "0"))
                body = json.loads(self.rfile.read(length))
                sub_key = self.headers.get(SUBMISSION_KEY_HEADER) or ""
                should_hold = False
                with server_ref.lock:
                    server_ref.seq += 1
                    n = server_ref.seq
                    run_id = f"run_{n:03d}"
                    conv_id = body.get("conversation_id") or f"conv_{n:03d}"
                    server_ref.total_posts += 1
                    server_ref.posts_by_key[sub_key] = (
                        server_ref.posts_by_key.get(sub_key, 0) + 1
                    )
                    if sub_key:
                        server_ref.by_submission_key[sub_key] = (run_id, conv_id)
                        server_ref.run_to_key[run_id] = sub_key
                    server_ref.run_to_payload[run_id] = {
                        "question": body["prompt"],
                        "conversation_id": conv_id,
                        "eval_run_id": body.get("eval_run_id"),
                    }
                    server_ref._append_capture_records(
                        eval_run_id=body.get("eval_run_id"),
                        run_id=run_id,
                        conversation_id=conv_id,
                        question=body["prompt"],
                        seq=n,
                    )
                    if (
                        server_ref.hold_post_for_key
                        and sub_key == server_ref.hold_post_for_key
                        and not server_ref._held_post_once
                    ):
                        server_ref._held_post_once = True
                        should_hold = True

                if should_hold:
                    # App has accepted and recorded the run, but response has not yet reached the worker.
                    server_ref.crash_ready.set()
                    server_ref.release_hold.wait(timeout=15)
                    return

                out = json.dumps(
                    {
                        "run_id": run_id,
                        "conversation_id": conv_id,
                        "message_id": f"msg_{n:03d}",
                        "events_url": f"/api/runs/{run_id}/events",
                    }
                ).encode()
                self.send_response(202)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def do_GET(self):
                if self.path.startswith("/api/runs/by-submission/"):
                    key = unquote(self.path.split("/api/runs/by-submission/", 1)[1])
                    with server_ref.lock:
                        server_ref.lookup_requests.append(key)
                        found = (
                            server_ref.by_submission_key.get(key)
                            if server_ref.lookup_enabled
                            else None
                        )
                    if not found:
                        body = b'{"detail":"not found"}'
                        self.send_response(404)
                        self.send_header("content-type", "application/json")
                        self.send_header("content-length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    body = json.dumps(
                        {"run_id": found[0], "conversation_id": found[1]}
                    ).encode()
                    self.send_response(200)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return

                if self.path.startswith("/api/runs/") and self.path.endswith("/events"):
                    run_id = self.path.split("/api/runs/", 1)[1].rsplit("/events", 1)[0]
                    should_hold = False
                    with server_ref.lock:
                        server_ref.events_get_count[run_id] = (
                            server_ref.events_get_count.get(run_id, 0) + 1
                        )
                        sub_key = server_ref.run_to_key.get(run_id, "")
                        info = server_ref.run_to_payload.get(run_id, {})
                        if (
                            server_ref.hold_events_for_key
                            and sub_key == server_ref.hold_events_for_key
                            and not server_ref._held_events_once
                        ):
                            server_ref._held_events_once = True
                            should_hold = True

                    self.send_response(200)
                    self.send_header("content-type", "text/event-stream")
                    self.send_header("connection", "close")
                    self.end_headers()
                    try:
                        self.wfile.write(b"event: run.started\ndata: {}\n\n")
                        self.wfile.flush()
                        if should_hold:
                            server_ref.crash_ready.set()
                            server_ref.release_hold.wait(timeout=15)
                            return
                        _, _, _, tool_output = _capture_fixture_for_question(
                            info.get("question", "")
                        )
                        reduced = json.dumps(
                            {
                                "payload": {
                                    "working_context": [
                                        {
                                            "role": "tool",
                                            "content": json.dumps(tool_output),
                                        }
                                    ]
                                }
                            }
                        )
                        self.wfile.write(
                            f"event: context.reduced\ndata: {reduced}\n\n"
                            "event: run.completed\ndata: {}\n\n".encode()
                        )
                        self.wfile.flush()
                    except OSError:
                        pass
                    return

                self.send_response(404)
                self.send_header("content-length", "0")
                self.end_headers()

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_port}"

    def _append_capture_records(
        self,
        *,
        eval_run_id: str | None,
        run_id: str,
        conversation_id: str,
        question: str,
        seq: int,
    ) -> None:
        tool_name, tool_args, answer, tool_output = _capture_fixture_for_question(
            question
        )
        cap_file = self.capture_dir / "default" / "captures.jsonl"
        cap_file.parent.mkdir(parents=True, exist_ok=True)
        ts = f"2026-11-20T00:00:{seq:02d}+00:00"
        tool_rec = {
            "schema_version": "capture.v1",
            "record_type": "tool_call",
            "eval_run_id": eval_run_id,
            "run_id": run_id,
            "conversation_id": conversation_id,
            "turn_id": f"msg_{seq:03d}",
            "call_id": f"tcall_{seq:03d}",
            "tool_call_id": f"tcall_{seq:03d}",
            "tool_name": tool_name,
            "arguments": tool_args,
            "argument_keys": sorted(tool_args),
            "status": "completed",
            "row_count": tool_output["row_count"],
            "query_id": tool_output["query_id"],
            "latency_ms": 5,
            "error": None,
            "execution_mode": "scripted",
            "provider_client": "FixtureServer",
            "run_started_at": ts,
            "run_status": "completed",
            "step": 2,
            "tenant_id": "default",
        }
        model_rec = {
            "schema_version": "capture.v1",
            "record_type": "model_call",
            "eval_run_id": eval_run_id,
            "run_id": run_id,
            "conversation_id": conversation_id,
            "turn_id": f"msg_{seq:03d}",
            "call_id": f"mcall_{seq:03d}",
            "role": "answer",
            "question": question,
            "answer": answer,
            "model_alias": "taxi-answer",
            "provider_model_id": "taxi-answer",
            "provider_client": "FixtureServer",
            "input_tokens": 100,
            "output_tokens": 25,
            "cost_usd": 0.00001,
            "cost_kind": "estimated",
            "pricing_version": "fixture-v1",
            "fallback_used": False,
            "finish_reason": "stop",
            "latency_ms": 15,
            "error": None,
            "execution_mode": "scripted",
            "run_started_at": ts,
            "run_status": "completed",
            "step": 4,
            "tenant_id": "default",
        }
        with cap_file.open("a") as fh:
            fh.write(json.dumps(tool_rec) + "\n")
            fh.write(json.dumps(model_rec) + "\n")

    def close(self) -> None:
        self.release_hold.set()
        self._httpd.shutdown()
        self._httpd.server_close()


def _init_run(root: Path, label: str, web: str, cases: list[str]) -> tuple[str, Path]:
    assert (
        main(
            [
                "run-start",
                "--root",
                str(root),
                "--label",
                label,
                "--web",
                web,
                "--cases",
                *cases,
            ]
        )
        == 0
    )
    run_id = (root / ".current").read_text().strip()
    return run_id, root / run_id


def _spawn_worker(target: str, task_queue: str) -> subprocess.Popen:
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "evals.cli",
            "temporal-worker",
            "--address",
            target,
            "--task-queue",
            task_queue,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _stop_worker(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)


def _rows_of(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@pytest.fixture(scope="module")
def temporal_target():
    """Start one local Temporal test server shared by the OS-worker SIGKILL proof tests."""
    loop = asyncio.new_event_loop()
    env = loop.run_until_complete(WorkflowEnvironment.start_local())
    target = env.client.service_client.config.target_host
    try:
        yield target
    finally:
        loop.run_until_complete(env.shutdown())
        loop.close()


def test_sigkill_post_accept_reconciles_and_produces_one_report(
    tmp_path: Path, temporal_target: str
):
    """Scenario 1: SIGKILL worker after POST 202 acceptance and before Activity ack.

    Proves:
    - Completed Case 1 (det-001) is never rerun (POST count == 1).
    - Accepted in-flight Case 2 turn 0 (multi-001#0) is reconciled via SSE replay (POST count == 1).
    - Case 2 turn 1 continues in the same conversation_id, and Case 3 (det-004) completes.
    - One final graded report (captures.snapshot.jsonl, report/*.json/.md, review.html) is produced.
    """
    root = tmp_path / "evals"
    capture_dir = tmp_path / "_capture"
    server = _FixtureEvalServer(capture_dir, lookup_enabled=False)
    run_id, run_dir = _init_run(root, "sigkill-post-accept", server.url, PILOT_CASES)
    target_key = submission_key(run_id, "multi-001", 0)
    server.hold_events_for_key = target_key
    _, wanted, expected, cases_path = resolve_batch_plan(run_dir, server.url, [], False)

    tq = f"pilot-post-accept-{int(time.time() * 1000)}"
    w1 = _spawn_worker(temporal_target, tq)
    w2: subprocess.Popen | None = None
    try:

        async def drive():
            nonlocal w2
            task = asyncio.create_task(
                run_temporal_eval(
                    str(run_dir),
                    run_id,
                    server.url,
                    wanted,
                    expected,
                    cases_path=str(cases_path),
                    timeout_s=30,
                    heartbeat_timeout_s=2.0,
                    finalize=True,
                    capture_dir=str(capture_dir),
                    address=temporal_target,
                    task_queue=tq,
                )
            )
            assert await asyncio.to_thread(
                server.crash_ready.wait, 20
            ), "worker #1 never reached multi-001#0 SSE stream"
            # Confirm Case 1 already checkpointed and Case 2 turn 0 is already journaled as accepted.
            journal_before = load_submission_journal(run_dir)
            assert journal_before[target_key]["status"] == "accepted"
            assert [r["case_id"] for r in _rows_of(run_dir / "results.jsonl")] == [
                "det-001"
            ]

            os.kill(w1.pid, signal.SIGKILL)
            w1.wait(timeout=5)
            server.release_hold.set()

            w2 = _spawn_worker(temporal_target, tq)
            return await asyncio.wait_for(task, timeout=30)

        result = asyncio.run(drive())
    finally:
        _stop_worker(w1)
        _stop_worker(w2)
        server.close()

    assert result.status == "complete"
    assert result.finalization.finalized is True
    assert server.total_posts == 4
    for cid, idx in expected:
        assert server.posts_by_key[submission_key(run_id, cid, idx)] == 1

    # multi-001#0 SSE was opened by Worker #1 and replayed by Worker #2.
    reconciled_run_id = server.by_submission_key[target_key][0]
    assert server.events_get_count[reconciled_run_id] >= 2
    by_case = {c.case_id: c for c in result.cases}
    assert by_case["multi-001"].reconciled_turns == 1

    rows = _rows_of(run_dir / "results.jsonl")
    assert len(rows) == 4
    multi_rows = sorted(
        [r for r in rows if r["case_id"] == "multi-001"], key=lambda r: r["turn_index"]
    )
    assert len(multi_rows) == 2
    assert multi_rows[0]["conversation_id"] == multi_rows[1]["conversation_id"]

    state = json.loads((run_dir / "state.json").read_text())
    assert state["status"] == "complete"
    assert state["turns_total"] == 4 and state["turns_recorded"] == 4
    assert state["incomplete_turns"] == [] and state["missing_turns"] == []

    # Final graded report + review.html generated once with exact 4 turns.
    assert (run_dir / "captures.snapshot.jsonl").is_file()
    assert (run_dir / "captures.snapshot.sha256").is_file()
    assert (run_dir / "review.html").is_file()
    graded_turns, _ = load_run_reports(run_dir)
    assert set(graded_turns) == set(expected)
    review_html = (run_dir / "review.html").read_text()
    for cid, idx in expected:
        assert f"{cid}#{idx}" in review_html


def test_sigkill_blind_window_without_lookup_records_ambiguous_no_retry(
    tmp_path: Path, temporal_target: str
):
    """Scenario 2: SIGKILL in POST blind window when app has no submission lookup (404).

    Proves:
    - Worker #2 checks lookup (404), records ``ambiguous_submission`` + ``evidence='incomplete'``.
    - ``POST /api/runs`` count for the crashed turn remains 1 (ZERO blind retries!).
    - ``state.json`` ends as ``incomplete`` and finalization safely skips freeze/grade.
    """
    root = tmp_path / "evals"
    capture_dir = tmp_path / "_capture"
    server = _FixtureEvalServer(capture_dir, lookup_enabled=False)
    run_id, run_dir = _init_run(root, "sigkill-blind-404", server.url, PILOT_CASES)
    target_key = submission_key(run_id, "multi-001", 0)
    server.hold_post_for_key = target_key
    _, wanted, expected, cases_path = resolve_batch_plan(run_dir, server.url, [], False)

    tq = f"pilot-blind-404-{int(time.time() * 1000)}"
    w1 = _spawn_worker(temporal_target, tq)
    w2: subprocess.Popen | None = None
    try:

        async def drive():
            nonlocal w2
            task = asyncio.create_task(
                run_temporal_eval(
                    str(run_dir),
                    run_id,
                    server.url,
                    wanted,
                    expected,
                    cases_path=str(cases_path),
                    timeout_s=30,
                    heartbeat_timeout_s=2.0,
                    finalize=True,
                    capture_dir=str(capture_dir),
                    address=temporal_target,
                    task_queue=tq,
                )
            )
            assert await asyncio.to_thread(
                server.crash_ready.wait, 20
            ), "worker #1 never reached multi-001#0 POST blind window"
            journal_before = load_submission_journal(run_dir)
            assert journal_before[target_key]["status"] == "submitting"

            os.kill(w1.pid, signal.SIGKILL)
            w1.wait(timeout=5)
            server.release_hold.set()

            w2 = _spawn_worker(temporal_target, tq)
            return await asyncio.wait_for(task, timeout=30)

        result = asyncio.run(drive())
    finally:
        _stop_worker(w1)
        _stop_worker(w2)
        server.close()

    assert result.status == "incomplete"
    assert result.finalization.finalized is False
    # Exact POST counts: det-001#0=1, multi-001#0=1 (NO blind retry!), multi-001#1=0, det-004#0=1.
    assert server.posts_by_key[submission_key(run_id, "det-001", 0)] == 1
    assert server.posts_by_key[target_key] == 1
    assert server.posts_by_key.get(submission_key(run_id, "multi-001", 1), 0) == 0
    assert server.posts_by_key[submission_key(run_id, "det-004", 0)] == 1
    assert target_key in server.lookup_requests

    rows_by_turn = {
        (r["case_id"], r["turn_index"]): r for r in _rows_of(run_dir / "results.jsonl")
    }
    amb = rows_by_turn[("multi-001", 0)]
    assert amb["run_id"] is None
    assert amb["terminal"] == AMBIGUOUS_TERMINAL
    assert amb["evidence"] == "incomplete"
    assert load_submission_journal(run_dir)[target_key]["status"] == AMBIGUOUS_TERMINAL

    state = json.loads((run_dir / "state.json").read_text())
    assert state["status"] == "incomplete"
    assert state["incomplete_turns"] == [
        {"case_id": "multi-001", "turn_index": 0, "terminal": AMBIGUOUS_TERMINAL}
    ]
    assert state["missing_turns"] == [{"case_id": "multi-001", "turn_index": 1}]
    assert not (run_dir / "captures.snapshot.jsonl").exists()


def test_sigkill_blind_window_with_lookup_reconciles_zero_duplicates(
    tmp_path: Path, temporal_target: str
):
    """Scenario 3: SIGKILL in POST blind window when server exposes submission-key lookup (200).

    Proves:
    - Worker #2 resolves the existing run_id via ``GET /api/runs/by-submission/{key}``.
    - Reconciles via SSE without calling POST /api/runs again (POST count == 1 for all 4 turns).
    - Finishes multi-001#1 and det-004#0 and produces one complete graded report.
    """
    root = tmp_path / "evals"
    capture_dir = tmp_path / "_capture"
    server = _FixtureEvalServer(capture_dir, lookup_enabled=True)
    run_id, run_dir = _init_run(root, "sigkill-blind-200", server.url, PILOT_CASES)
    target_key = submission_key(run_id, "multi-001", 0)
    server.hold_post_for_key = target_key
    _, wanted, expected, cases_path = resolve_batch_plan(run_dir, server.url, [], False)

    tq = f"pilot-blind-200-{int(time.time() * 1000)}"
    w1 = _spawn_worker(temporal_target, tq)
    w2: subprocess.Popen | None = None
    try:

        async def drive():
            nonlocal w2
            task = asyncio.create_task(
                run_temporal_eval(
                    str(run_dir),
                    run_id,
                    server.url,
                    wanted,
                    expected,
                    cases_path=str(cases_path),
                    timeout_s=30,
                    heartbeat_timeout_s=2.0,
                    finalize=True,
                    capture_dir=str(capture_dir),
                    address=temporal_target,
                    task_queue=tq,
                )
            )
            assert await asyncio.to_thread(
                server.crash_ready.wait, 20
            ), "worker #1 never reached multi-001#0 POST blind window"
            assert (
                load_submission_journal(run_dir)[target_key]["status"] == "submitting"
            )

            os.kill(w1.pid, signal.SIGKILL)
            w1.wait(timeout=5)
            server.release_hold.set()

            w2 = _spawn_worker(temporal_target, tq)
            return await asyncio.wait_for(task, timeout=30)

        result = asyncio.run(drive())
    finally:
        _stop_worker(w1)
        _stop_worker(w2)
        server.close()

    assert result.status == "complete"
    assert result.finalization.finalized is True
    assert server.total_posts == 4
    for cid, idx in expected:
        assert server.posts_by_key[submission_key(run_id, cid, idx)] == 1

    journal_after = load_submission_journal(run_dir)
    assert journal_after[target_key]["status"] == "accepted"
    assert journal_after[target_key]["source"] == "lookup"

    rows = _rows_of(run_dir / "results.jsonl")
    multi_rows = sorted(
        [r for r in rows if r["case_id"] == "multi-001"], key=lambda r: r["turn_index"]
    )
    assert len(multi_rows) == 2
    assert multi_rows[0]["conversation_id"] == multi_rows[1]["conversation_id"]

    graded_turns, _ = load_run_reports(run_dir)
    assert set(graded_turns) == set(expected)
    assert (run_dir / "review.html").is_file()


def test_store_skips_completed_case_with_zero_http_calls(tmp_path: Path):
    """Re-invoking execute_case_reconcilable for a finished case makes zero network calls."""
    run_dir = tmp_path / "run-skip"
    run_dir.mkdir()
    case = next(c for c in load_cases_raw() if c["id"] == "det-001")
    (run_dir / "results.jsonl").write_text(
        json.dumps(
            {
                "case_id": "det-001",
                "turn_index": 0,
                "question": case["turns"][0]["question"],
                "conversation_id": "conv_existing",
                "run_id": "run_existing",
                "terminal": "run.completed",
                "evidence": "complete",
                "wall_s": 0.1,
            }
        )
        + "\n"
    )
    # Unreachable port guarantees any accidental HTTP call would fail.
    res = execute_case_reconcilable(
        run_dir,
        "run-skip",
        "http://127.0.0.1:1",
        case,
        [("det-001", 0)],
        timeout_s=2,
    )
    assert res["status"] == "complete"
    assert res["skipped_already_complete"] is True
    assert res["turns_completed"] == 1


def test_cli_lazy_import_and_missing_temporalio_message(monkeypatch, capsys):
    """Default evals commands never import temporalio; missing temporalio yields a clear RunError."""
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from evals.cli import main; "
                "rc = main(['validate']); assert rc == 0; "
                "assert 'temporalio' not in sys.modules; "
                "assert 'evals.temporal_pilot' not in sys.modules"
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "ok:" in proc.stdout

    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "temporalio" or name.startswith("temporalio."):
            raise ModuleNotFoundError("No module named 'temporalio'", name="temporalio")
        return real_import(name, *args, **kwargs)

    monkeypatch.delitem(sys.modules, "temporalio", raising=False)
    monkeypatch.delitem(sys.modules, "evals.temporal_pilot", raising=False)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert main(["temporal-worker"]) == 2
    err = capsys.readouterr().err
    assert "--extra temporal" in err


def test_workflow_contains_no_direct_network_or_filesystem_calls():
    """Guardrail: EvalBatchWorkflow itself must stay strictly deterministic (no I/O)."""
    src = inspect.getsource(EvalBatchWorkflow)
    for forbidden in (
        "urllib",
        "http.client",
        "open(",
        "Path(",
        "read_text",
        "write_text",
    ):
        assert forbidden not in src
