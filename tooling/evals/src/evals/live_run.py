"""Drive golden cases through the running app, recording into a run folder.

One fresh conversation per case; no retries. A turn that does not reach a terminal event ends its case.
Nothing here touches the network at import time.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from evals.runs import RunError

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
    """Stream SSE until a terminal event or timeout; return (raw_text, terminal|'timeout')."""
    deadline = time.monotonic() + timeout_s
    raw, terminal = [], "timeout"
    try:
        with urllib.request.urlopen(
            f"{web}/api/runs/{run_id}/events", timeout=timeout_s
        ) as r:
            for line in r:
                text = line.decode(errors="replace")
                raw.append(text)
                for t in TERMINAL:
                    if t in text:
                        terminal = t
                if terminal != "timeout" or time.monotonic() > deadline:
                    break
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raw.append(f"[driver] stream error: {e!r}\n")
    return "".join(raw), terminal


def run_live(
    run_dir: Path,
    run_id: str,
    web: str | None,
    case_ids: list[str],
    all_cases: bool,
    smoke: bool,
    timeout: int,
    cases_path: Path = CASES,
) -> Path:
    manifest_p = run_dir / "manifest.json"
    if not manifest_p.is_file():
        raise RunError(f"{manifest_p} missing; run `evals run-start` first")
    manifest = json.loads(manifest_p.read_text())
    out = run_dir / "smoke" if smoke else run_dir
    if not smoke and (out / "results.jsonl").exists():
        raise RunError(
            f"{out / 'results.jsonl'} exists; start a new run (no overwrite)"
        )
    web = web or manifest.get("web") or "http://localhost:3100"
    cases = [json.loads(x) for x in cases_path.read_text().splitlines() if x.strip()]
    by_id = {c["id"]: c for c in cases}
    wanted = (
        [c["id"] for c in cases]
        if all_cases
        else case_ids or manifest.get("cases") or []
    )
    unknown = [c for c in wanted if c not in by_id]
    if unknown or not wanted:
        raise RunError(f"unknown or empty case list: {unknown}")
    eval_run_id = f"{run_id}-smoke" if smoke else run_id
    (out / "events").mkdir(parents=True, exist_ok=True)

    matches, rows = [], []
    for cid in wanted:
        conv = None
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
            except (
                OSError,
                ValueError,
                KeyError,
            ) as e:  # recorded, not retried (URLError is an OSError)
                row.update(
                    run_id=None,
                    terminal=f"submit_error: {e!r}",
                    wall_s=round(time.monotonic() - t0, 2),
                )
                rows.append(row)
                print(f"{cid}[{idx}] SUBMIT ERROR {e!r}")
                break
            conv = acc["conversation_id"]
            raw, terminal = read_events(web, acc["run_id"], timeout)
            (out / "events" / f"{acc['run_id']}.txt").write_text(raw)
            row.update(
                conversation_id=conv,
                run_id=acc["run_id"],
                terminal=terminal,
                wall_s=round(time.monotonic() - t0, 2),
            )
            rows.append(row)
            matches.append(
                {
                    "run_id": acc["run_id"],
                    "case_id": cid,
                    "turn_index": idx,
                    "confirmed_by": CONFIRMED_BY,
                }
            )
            print(
                f"{cid}[{idx}] {acc['run_id']} conv={conv} {terminal} {row['wall_s']}s"
            )
            if terminal == "timeout":
                break  # do not feed later turns on a run that never finished
    (out / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (out / "matches.json").write_text(
        json.dumps({"schema": "evals.matches.v1", "matches": matches}, indent=1)
    )
    return out
