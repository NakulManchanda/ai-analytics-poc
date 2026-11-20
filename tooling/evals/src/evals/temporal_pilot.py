"""Opt-in local Temporal workflow, activities, worker, and starter for offline eval runs (#253).

Owns Temporal orchestration only:
- ``EvalBatchWorkflow`` schedules one ``execute_case_activity`` per whole multi-turn golden case,
  then invokes ``finalize_eval_activity`` to write final ``state.json`` and optionally run
  ``run-freeze``, ``run-grade``, and ``run-review``.
- Workflow code is strictly deterministic: zero network calls, zero filesystem I/O, and zero
  per-tool retries.
- Online request execution (``FastAPI -> MCP``) and Redis ``app.worker`` live elsewhere and are
  untouched.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

DEFAULT_ADDRESS = "localhost:7233"
DEFAULT_NAMESPACE = "default"
DEFAULT_TASK_QUEUE = "eval-pilot"


@dataclass
class CaseActivityInput:
    run_dir: str
    eval_run_id: str
    web: str
    case_id: str
    expected_turns: list[list]
    cases_path: str | None = None
    timeout_s: int = 120
    heartbeat_interval_s: float = 1.0


@dataclass
class CaseActivityResult:
    case_id: str
    status: str
    skipped_already_complete: bool = False
    reconciled_turns: int = 0
    ambiguous_turns: int = 0
    turns_completed: int = 0


@dataclass
class FinalizeInput:
    run_dir: str
    eval_run_id: str
    expected_turns: list[list]
    finalize: bool = False
    capture_dir: str | None = None


@dataclass
class FinalizeResult:
    status: str
    turns_total: int
    turns_recorded: int
    incomplete_count: int
    missing_count: int
    finalized: bool = False
    report_files: list[str] = field(default_factory=list)


@dataclass
class EvalBatchInput:
    run_dir: str
    eval_run_id: str
    web: str
    case_ids: list[str]
    expected_turns: list[list]
    cases_path: str | None = None
    timeout_s: int = 120
    heartbeat_timeout_s: float = 15.0
    finalize: bool = False
    capture_dir: str | None = None


@dataclass
class EvalBatchResult:
    eval_run_id: str
    status: str
    cases: list[CaseActivityResult]
    finalization: FinalizeResult


async def _heartbeat_loop(interval_s: float, state: dict[str, str]) -> None:
    while True:
        activity.heartbeat(state["phase"])
        await asyncio.sleep(interval_s)


@activity.defn
async def execute_case_activity(inp: CaseActivityInput) -> CaseActivityResult:
    """Run or reconcile one whole multi-turn golden case with periodic activity heartbeats."""
    from pathlib import Path

    from evals.temporal_store import execute_case_reconcilable, load_cases_raw

    cases = load_cases_raw(Path(inp.cases_path) if inp.cases_path else None)
    by_id = {c["id"]: c for c in cases}
    if inp.case_id not in by_id:
        raise ValueError(f"unknown case_id {inp.case_id!r}")
    expected = [(str(c), int(i)) for c, i in inp.expected_turns]

    phase = {"phase": f"{inp.case_id}:init"}
    hb_interval = max(0.2, min(inp.heartbeat_interval_s, 10.0))
    hb_task = asyncio.create_task(_heartbeat_loop(hb_interval, phase))
    try:
        res = await asyncio.to_thread(
            execute_case_reconcilable,
            Path(inp.run_dir),
            inp.eval_run_id,
            inp.web,
            by_id[inp.case_id],
            expected,
            inp.timeout_s,
            lambda msg: phase.__setitem__("phase", msg),
        )
    finally:
        hb_task.cancel()
        try:
            await hb_task
        except asyncio.CancelledError:
            pass

    return CaseActivityResult(
        case_id=res["case_id"],
        status=res["status"],
        skipped_already_complete=bool(res["skipped_already_complete"]),
        reconciled_turns=int(res["reconciled_turns"]),
        ambiguous_turns=int(res["ambiguous_turns"]),
        turns_completed=int(res["turns_completed"]),
    )


@activity.defn
async def finalize_eval_activity(inp: FinalizeInput) -> FinalizeResult:
    """Write final ``state.json`` and, when requested and complete, freeze/grade/render review."""
    import argparse
    from pathlib import Path

    from evals.runs_cli import run_freeze, run_grade, run_review_cmd
    from evals.temporal_store import update_run_state

    run_dir = Path(inp.run_dir)
    expected = [(str(c), int(i)) for c, i in inp.expected_turns]
    state = update_run_state(run_dir, expected, status="final")

    finalized = False
    report_files: list[str] = []
    if inp.finalize and state["status"] == "complete":
        root = run_dir.parent
        snap = run_dir / "captures.snapshot.jsonl"
        if not snap.is_file():
            run_freeze(
                argparse.Namespace(
                    root=root,
                    run=inp.eval_run_id,
                    allow_incomplete=False,
                    capture_dir=Path(inp.capture_dir) if inp.capture_dir else None,
                )
            )
        run_grade(
            argparse.Namespace(
                root=root,
                run=inp.eval_run_id,
                allow_incomplete=False,
            )
        )
        run_review_cmd(
            argparse.Namespace(
                root=root,
                run=inp.eval_run_id,
                allow_incomplete=False,
                baseline_results=None,
                phoenix_url=None,
                phoenix_project_id="default",
            )
        )
        finalized = True
        if snap.is_file():
            report_files.append("captures.snapshot.jsonl")
        report_dir = run_dir / "report"
        if report_dir.is_dir():
            report_files.extend(
                sorted(
                    str(p.relative_to(run_dir))
                    for p in report_dir.iterdir()
                    if p.is_file()
                )
            )
        if (run_dir / "review.html").is_file():
            report_files.append("review.html")

    return FinalizeResult(
        status=state["status"],
        turns_total=int(state["turns_total"]),
        turns_recorded=int(state["turns_recorded"]),
        incomplete_count=len(state["incomplete_turns"]),
        missing_count=len(state["missing_turns"]),
        finalized=finalized,
        report_files=report_files,
    )


@workflow.defn
class EvalBatchWorkflow:
    """Deterministic Temporal workflow scheduling whole multi-turn eval cases as Activities."""

    @workflow.run
    async def run(self, inp: EvalBatchInput) -> EvalBatchResult:
        hb_timeout = timedelta(seconds=max(1.0, inp.heartbeat_timeout_s))
        hb_interval = max(0.2, min(inp.heartbeat_timeout_s / 3.0, 5.0))
        # Per-case activity timeout accommodates multi-turn cases (up to 4 turns * timeout_s + buffer).
        case_timeout = timedelta(seconds=max(30, inp.timeout_s * 4 + 30))
        retry_policy = RetryPolicy(
            initial_interval=timedelta(milliseconds=300),
            backoff_coefficient=1.5,
            maximum_interval=timedelta(seconds=5),
            maximum_attempts=5,
        )

        case_results: list[CaseActivityResult] = []
        for cid in inp.case_ids:
            res = await workflow.execute_activity(
                execute_case_activity,
                CaseActivityInput(
                    run_dir=inp.run_dir,
                    eval_run_id=inp.eval_run_id,
                    web=inp.web,
                    case_id=cid,
                    expected_turns=inp.expected_turns,
                    cases_path=inp.cases_path,
                    timeout_s=inp.timeout_s,
                    heartbeat_interval_s=hb_interval,
                ),
                start_to_close_timeout=case_timeout,
                heartbeat_timeout=hb_timeout,
                retry_policy=retry_policy,
            )
            case_results.append(res)

        finalization = await workflow.execute_activity(
            finalize_eval_activity,
            FinalizeInput(
                run_dir=inp.run_dir,
                eval_run_id=inp.eval_run_id,
                expected_turns=inp.expected_turns,
                finalize=inp.finalize,
                capture_dir=inp.capture_dir,
            ),
            start_to_close_timeout=timedelta(seconds=120),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(milliseconds=300),
                maximum_interval=timedelta(seconds=3),
                maximum_attempts=3,
            ),
        )
        return EvalBatchResult(
            eval_run_id=inp.eval_run_id,
            status=finalization.status,
            cases=case_results,
            finalization=finalization,
        )


def workflow_id_for_run(eval_run_id: str) -> str:
    return f"eval-{eval_run_id}"


async def run_temporal_worker(
    address: str = DEFAULT_ADDRESS,
    namespace: str = DEFAULT_NAMESPACE,
    task_queue: str = DEFAULT_TASK_QUEUE,
) -> None:
    """Connect a standalone Temporal worker process serving ``EvalBatchWorkflow`` and its activities."""
    from temporalio.client import Client
    from temporalio.worker import Worker

    client = await Client.connect(address, namespace=namespace)
    worker = Worker(
        client,
        task_queue=task_queue,
        workflows=[EvalBatchWorkflow],
        activities=[execute_case_activity, finalize_eval_activity],
    )
    await worker.run()


async def run_temporal_eval(
    run_dir: str,
    eval_run_id: str,
    web: str,
    case_ids: list[str],
    expected_turns: list[tuple[str, int]],
    *,
    cases_path: str | None = None,
    timeout_s: int = 120,
    heartbeat_timeout_s: float = 15.0,
    finalize: bool = False,
    capture_dir: str | None = None,
    address: str = DEFAULT_ADDRESS,
    namespace: str = DEFAULT_NAMESPACE,
    task_queue: str = DEFAULT_TASK_QUEUE,
) -> EvalBatchResult:
    """Start (or attach to) the deterministic ``EvalBatchWorkflow`` for ``eval_run_id`` and await its result."""
    from pathlib import Path

    from temporalio.client import Client
    from temporalio.exceptions import WorkflowAlreadyStartedError

    from evals.temporal_store import update_run_state

    # Record initial "running" state before handing off to Temporal, matching run-live semantics.
    update_run_state(Path(run_dir), expected_turns, status="running")

    client = await Client.connect(address, namespace=namespace)
    wf_id = workflow_id_for_run(eval_run_id)
    batch_input = EvalBatchInput(
        run_dir=str(Path(run_dir).resolve()),
        eval_run_id=eval_run_id,
        web=web,
        case_ids=list(case_ids),
        expected_turns=[[c, i] for c, i in expected_turns],
        cases_path=str(Path(cases_path).resolve()) if cases_path else None,
        timeout_s=timeout_s,
        heartbeat_timeout_s=heartbeat_timeout_s,
        finalize=finalize,
        capture_dir=str(Path(capture_dir).resolve()) if capture_dir else None,
    )
    try:
        handle = await client.start_workflow(
            EvalBatchWorkflow.run,
            batch_input,
            id=wf_id,
            task_queue=task_queue,
        )
    except WorkflowAlreadyStartedError:
        handle = client.get_workflow_handle(wf_id)
    return await handle.result()
