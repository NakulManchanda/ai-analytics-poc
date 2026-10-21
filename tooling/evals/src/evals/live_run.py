"""Drive golden cases through the running app, recording into a run folder.

One fresh conversation per case; no retries. A turn that does not reach a terminal event ends its case.
Nothing here touches the network at import time.
"""

from __future__ import annotations

import http.client
import json
import os
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from evals.runs import RunError, read_state, write_json_atomic

CASES = Path("tooling/evals/datasets/golden/v1/cases.jsonl")
TERMINAL = ("run.completed", "run.failed", "run.cancelled")
CONFIRMED_BY = (
    "operator-driver evals run-live: run-to-turn wiring only, not a golden review"
)


def post_run(
    web: str, prompt: str, conversation_id: str | None, eval_run_id: str | None
) -> dict:
    body: dict = {"prompt": prompt}
    if conversation_id:
        body["conversation_id"] = conversation_id
    if eval_run_id:
        body["eval_run_id"] = eval_run_id
    req = urllib.request.Request(
        f"{web}/api/runs",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def read_events(web: str, run_id: str, timeout_s: int) -> tuple[str, str]:
    """Read SSE frames until a terminal ``event:`` or the overall deadline.

    Returns (raw_text, terminal | 'timeout' | 'stream_error'). Each read is bounded by the time left,
    so neither a stalled nor a trickling stream can outlive ``timeout_s``.
    """
    deadline = time.monotonic() + timeout_s
    u = urlsplit(web)
    cls = (
        http.client.HTTPSConnection
        if u.scheme == "https"
        else http.client.HTTPConnection
    )
    raw: list[str] = []
    frame: list[str] = []
    terminal = "timeout"
    conn = cls(u.hostname, u.port, timeout=min(timeout_s, 30))
    try:
        conn.request("GET", f"/api/runs/{run_id}/events")
        sock = conn.sock  # keep: conn.sock is cleared for will-close responses
        resp = conn.getresponse()
        if resp.status != 200:
            raise OSError(f"events HTTP {resp.status}")
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            sock.settimeout(left)
            line = resp.readline().decode(errors="replace")
            if not line:
                terminal = "stream_error"  # closed before a terminal event
                raw.append("[driver] stream closed without terminal event\n")
                break
            raw.append(line)
            if line.strip():
                frame.append(line.rstrip("\r\n"))
                continue
            event = next((f[6:].strip() for f in frame if f.startswith("event:")), "")
            frame = []
            if event in TERMINAL:
                terminal = event
                break
    except (OSError, http.client.HTTPException) as e:
        if time.monotonic() < deadline:
            terminal = "stream_error"
        raw.append(f"[driver] stream error: {e!r}\n")
    finally:
        conn.close()
    return "".join(raw), terminal


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _append_row(path: Path, row: dict) -> None:
    with path.open("a") as f:
        f.write(json.dumps(row) + "\n")
        f.flush()
        os.fsync(f.fileno())


def _read_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def _write_rows(path: Path, rows: list[dict]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in rows))
    tmp.replace(path)


def _write_matches(out: Path, rows: list[dict]) -> None:
    matches = [
        {
            "run_id": r["run_id"],
            "case_id": r["case_id"],
            "turn_index": r["turn_index"],
            "confirmed_by": CONFIRMED_BY,
        }
        for r in rows
        if r.get("run_id")
    ]
    write_json_atomic(
        out / "matches.json", {"schema": "evals.matches.v1", "matches": matches}
    )


def _evidence(row: dict) -> str:
    return row.get("evidence") or (
        "complete" if row.get("terminal") in TERMINAL else "incomplete"
    )


def run_live(
    run_dir: Path,
    run_id: str,
    web: str | None,
    case_ids: list[str],
    all_cases: bool,
    smoke: bool,
    timeout: int,
    cases_path: Path = CASES,
    resume: bool = False,
) -> tuple[Path, list[dict]]:
    """Run the cases; return (output dir, incomplete turn records)."""
    manifest_p = run_dir / "manifest.json"
    if not manifest_p.is_file():
        raise RunError(f"{manifest_p} missing; run `evals run-start` first")
    manifest = json.loads(manifest_p.read_text())
    out = run_dir / "smoke" if smoke else run_dir
    results_p = out / "results.jsonl"
    old_state = read_state(out)
    if not smoke and not resume and (old_state is not None or results_p.exists()):
        raise RunError(
            f"{out} already has run state/results; start a new run, or pass --resume "
            "to re-run incomplete cases (no overwrite)"
        )
    web = web or manifest.get("web") or "http://localhost:3100"
    cases = [json.loads(x) for x in cases_path.read_text().splitlines() if x.strip()]
    by_id = {c["id"]: c for c in cases}
    wanted = (
        [c["id"] for c in cases]
        if all_cases
        else case_ids
        or manifest.get("cases")
        or ([c["id"] for c in cases] if resume and not smoke else [])
    )
    unknown = [c for c in wanted if c not in by_id]
    if unknown or not wanted:
        raise RunError(f"unknown or empty case list: {unknown}")
    wanted_all = list(wanted)
    eval_run_id = f"{run_id}-smoke" if smoke else run_id
    (out / "events").mkdir(parents=True, exist_ok=True)

    rows = _read_rows(results_p) if resume else []
    if resume:
        keep, superseded, todo = [], [], []
        for cid in wanted:
            mine = [r for r in rows if r["case_id"] == cid]
            full = len(mine) == len(by_id[cid]["turns"])
            if mine and full and all(_evidence(r) == "complete" for r in mine):
                keep += mine
                continue
            superseded += mine
            todo.append(cid)
        keep += [r for r in rows if r["case_id"] not in wanted]
        if superseded:
            with (out / "results.superseded.jsonl").open("a") as f:
                for r in superseded:
                    f.write(json.dumps(dict(r, superseded_at=_now())) + "\n")
        rows = keep
        _write_rows(results_p, rows)
        wanted = todo
    scope = wanted_all if smoke else (manifest.get("cases") or list(by_id))
    expected = [(c, i) for c in scope for i in range(len(by_id[c]["turns"]))]
    started = (old_state or {}).get("started_at") or _now()
    mine_incomplete: list[dict] = []  # this invocation only (drives the exit code)

    def save(status: str) -> None:
        recorded = {(r["case_id"], r["turn_index"]): r for r in rows}
        inc = [
            {"case_id": c, "turn_index": i, "terminal": recorded[(c, i)]["terminal"]}
            for c, i in expected
            if (c, i) in recorded and _evidence(recorded[(c, i)]) != "complete"
        ]
        missing = [
            {"case_id": c, "turn_index": i}
            for c, i in expected
            if (c, i) not in recorded
        ]
        if status != "running":
            status = "incomplete" if inc or missing else "complete"
        write_json_atomic(
            out / "state.json",
            {
                "status": status,
                "started_at": started,
                "finished_at": None if status == "running" else _now(),
                "turns_total": len(expected),
                "turns_recorded": len(recorded),
                "incomplete_turns": inc,
                "missing_turns": missing,
            },
        )
        return status

    save("running")
    _write_matches(out, rows)
    for cid in wanted:
        conv = None
        prev_failed = False
        for idx, turn in enumerate(by_id[cid]["turns"]):
            t0 = time.monotonic()
            row = {
                "case_id": cid,
                "turn_index": idx,
                "question": turn["question"],
                "conversation_id": conv,
            }
            try:
                acc = post_run(web, turn["question"], conv, eval_run_id)
                conv = acc["conversation_id"]
                run_ref = acc["run_id"]
            except (OSError, ValueError, KeyError) as e:  # recorded, not retried
                row.update(run_id=None, terminal=f"submit_error: {e!r}")
                raw = terminal = None
            else:
                _append_row(
                    out / "submitted.jsonl",
                    {
                        "run_id": run_ref,
                        "case_id": cid,
                        "turn_index": idx,
                        "conversation_id": conv,
                        "ts": _now(),
                    },
                )
                raw, terminal = read_events(web, run_ref, timeout)
                (out / "events" / f"{run_ref}.txt").write_text(raw)
                row.update(conversation_id=conv, run_id=run_ref, terminal=terminal)
            row["evidence"] = "complete" if terminal in TERMINAL else "incomplete"
            # Turns after run.failed/run.cancelled still run on purpose (comparable turn counts, the
            # follow-up is its own measurement); the marker lets analysis separate cascades.
            if prev_failed:
                row["after_failed_turn"] = True
            prev_failed = terminal in ("run.failed", "run.cancelled")
            row["wall_s"] = round(time.monotonic() - t0, 2)
            _append_row(results_p, row)
            rows.append(row)
            _write_matches(out, rows)
            if row["evidence"] == "incomplete":
                mine_incomplete.append(
                    {"case_id": cid, "turn_index": idx, "terminal": row["terminal"]}
                )
            save("running")
            print(
                f"{cid}[{idx}] {row.get('run_id')} conv={conv} {row['terminal']} "
                f"evidence={row['evidence']} {row['wall_s']}s"
            )
            if row["evidence"] == "incomplete":
                break  # do not feed later turns on a run that never finished
    status = save("final")
    n_ok = sum(_evidence(r) == "complete" for r in rows)
    print(
        f"summary: status={status}; expected {len(expected)} turns, recorded {len(rows)}, "
        f"{n_ok} with complete evidence; {len(mine_incomplete)} incomplete in this invocation"
    )
    return out, mine_incomplete
