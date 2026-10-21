"""run-live against a local stdlib stub server; no real network."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import evals.live_run as lr
from evals.cli import main
from evals.live_run import read_events, run_live
from evals.runs import RunError

SEEN: list[dict] = []
MODE = {"events": "ok", "drop_post_at": None}


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        SEEN.append(body)
        n = len(SEEN)
        if MODE["drop_post_at"] == n:
            self.connection.close()  # drop mid-case
            return
        out = json.dumps(
            {
                "run_id": f"run_{n}",
                "conversation_id": body.get("conversation_id") or f"conv_{n}",
            }
        ).encode()
        self.send_response(200)
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_GET(self):
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        mode = MODE["events"]
        try:
            if mode == "ok":
                self.wfile.write(
                    b"event: run.started\ndata: {}\n\nevent: run.completed\ndata: {}\n\n"
                )
            elif mode == "failed":
                self.wfile.write(
                    b"event: run.started\ndata: {}\n\nevent: run.failed\ndata: {}\n\n"
                )
            elif mode == "text_only":  # terminal text inside data, never as an event
                self.wfile.write(b'event: run.step\ndata: {"msg": "run.completed"}\n\n')
            elif mode == "stall":
                self.wfile.write(b"event: run.started\ndata: {}\n\n")
                self.wfile.flush()
                time.sleep(3)
            elif mode == "trickle":
                for _ in range(40):
                    self.wfile.write(b": keepalive\n")
                    self.wfile.flush()
                    time.sleep(0.1)
        except OSError:
            pass


@pytest.fixture
def stub():
    SEEN.clear()
    MODE.update(events="ok", drop_post_at=None)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
def cases(tmp_path):
    p = tmp_path / "cases.jsonl"
    p.write_text(
        "".join(
            json.dumps({"id": cid, "turns": [{"question": "q1"}, {"question": "q2"}]})
            + "\n"
            for cid in ("c-1", "c-2")
        )
    )
    return p


def mk(tmp_path):
    run = tmp_path / "r-20200101T000000Z"
    run.mkdir()
    (run / "manifest.json").write_text("{}")
    return run


def rows_of(p):
    return [json.loads(x) for x in p.read_text().splitlines()]


def test_live_writes_results_and_state(tmp_path, stub, cases):
    run = mk(tmp_path)
    _out, inc = run_live(run, run.name, stub, ["c-1"], False, False, 5, cases)
    assert inc == []
    rows = rows_of(run / "results.jsonl")
    assert [r["turn_index"] for r in rows] == [0, 1]
    assert set(rows[0]) == {
        "case_id",
        "turn_index",
        "question",
        "conversation_id",
        "run_id",
        "terminal",
        "wall_s",
        "evidence",
    }
    assert rows[1]["conversation_id"] == rows[0]["conversation_id"]
    assert rows[0]["terminal"] == "run.completed" and rows[0]["evidence"] == "complete"
    assert (run / "events" / "run_1.txt").is_file()
    assert len(json.loads((run / "matches.json").read_text())["matches"]) == 2
    assert {b["eval_run_id"] for b in SEEN} == {run.name}
    st = json.loads((run / "state.json").read_text())
    assert (
        st["status"] == "complete"
        and st["turns_total"] == 2
        and st["turns_recorded"] == 2
    )
    assert st["finished_at"] and st["incomplete_turns"] == []
    with pytest.raises(RunError):  # no overwrite without --resume
        run_live(run, run.name, stub, ["c-1"], False, False, 5, cases)


def test_run_failed_is_valid_evidence(tmp_path, stub, cases):
    MODE["events"] = "failed"
    run = mk(tmp_path)
    _, inc = run_live(run, run.name, stub, ["c-1"], False, False, 5, cases)
    assert inc == []
    rows = rows_of(run / "results.jsonl")
    assert len(rows) == 2 and {r["terminal"] for r in rows} == {"run.failed"}
    assert {r["evidence"] for r in rows} == {"complete"}


def test_smoke_isolated(tmp_path, stub, cases):
    run = mk(tmp_path)
    run_live(run, run.name, stub, [], True, True, 5, cases)
    assert not (run / "results.jsonl").exists() and not (run / "state.json").exists()
    assert (run / "smoke" / "results.jsonl").is_file()
    assert {b["eval_run_id"] for b in SEEN} == {run.name + "-smoke"}


def test_requires_manifest(tmp_path, cases):
    run = tmp_path / "x"
    run.mkdir()
    with pytest.raises(RunError):
        run_live(run, "x", "http://127.0.0.1:1", ["c-1"], False, False, 1, cases)


def test_sse_terminal_text_inside_data_is_not_terminal(stub):
    MODE["events"] = "text_only"
    raw, term = read_events(stub, "run_x", 5)
    assert (
        "run.completed" in raw and term == "stream_error"
    )  # closed with no terminal frame


def test_sse_stall_and_trickle_respect_deadline(stub):
    for mode in ("stall", "trickle"):
        MODE["events"] = mode
        t0 = time.monotonic()
        _, term = read_events(stub, "run_x", 1)
        assert term == "timeout" and time.monotonic() - t0 < 2.5, mode


def test_incomplete_exit_code_and_resume(tmp_path, stub, cases, monkeypatch, capsys):
    """Stub drops the 2nd POST (c-1 turn 1): incomplete, exit 3; --resume re-runs only that case."""
    MODE["drop_post_at"] = 2
    root = tmp_path / "root"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(lr, "CASES", cases)
    monkeypatch.setattr(lr.run_live, "__defaults__", (cases, False))
    assert main(["run-start", "--root", str(root), "--label", "r"]) == 0
    rid = (root / ".current").read_text()
    run = root / rid
    base = ["--root", str(root), "--web", stub]
    assert main(["run-live", *base, "--all"]) == 3
    st = json.loads((run / "state.json").read_text())
    assert st["status"] == "incomplete"
    assert st["incomplete_turns"][0]["case_id"] == "c-1"
    rows = rows_of(run / "results.jsonl")
    assert [(r["case_id"], r["evidence"]) for r in rows] == [
        ("c-1", "complete"),
        ("c-1", "incomplete"),
        ("c-2", "complete"),
        ("c-2", "complete"),
    ]
    # downstream refuses incomplete evidence
    assert main(["run-freeze", "--root", str(root)]) == 2
    # re-run without --resume is refused
    assert main(["run-live", *base, "--all"]) == 2
    # resume: c-2 kept, c-1 superseded and re-run in a fresh conversation
    MODE["drop_post_at"] = None
    old_conv = rows[0]["conversation_id"]
    assert main(["run-live", *base, "--all", "--resume"]) == 0
    sup = rows_of(run / "results.superseded.jsonl")
    assert [r["case_id"] for r in sup] == ["c-1", "c-1"]
    final = rows_of(run / "results.jsonl")
    assert sorted((r["case_id"], r["turn_index"]) for r in final) == [
        ("c-1", 0),
        ("c-1", 1),
        ("c-2", 0),
        ("c-2", 1),
    ]
    c1 = [r for r in final if r["case_id"] == "c-1"]
    assert c1[0]["conversation_id"] != old_conv and {r["evidence"] for r in c1} == {
        "complete"
    }
    assert json.loads((run / "state.json").read_text())["status"] == "complete"
    assert len(json.loads((run / "matches.json").read_text())["matches"]) == 4


def test_crash_leaves_running_and_resume_recovers(tmp_path, stub, cases, monkeypatch):
    run = mk(tmp_path)
    real = lr.read_events
    calls = {"n": 0}

    def boom(*a):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt
        return real(*a)

    monkeypatch.setattr(lr, "read_events", boom)
    with pytest.raises(KeyboardInterrupt):
        run_live(run, run.name, stub, ["c-1"], False, False, 5, cases)
    assert json.loads((run / "state.json").read_text())["status"] == "running"
    assert len(rows_of(run / "results.jsonl")) == 1  # checkpointed before the crash
    monkeypatch.setattr(lr, "read_events", real)
    with pytest.raises(RunError):
        run_live(run, run.name, stub, ["c-1"], False, False, 5, cases)
    _, inc = run_live(run, run.name, stub, ["c-1"], False, False, 5, cases, resume=True)
    assert inc == [] and len(rows_of(run / "results.jsonl")) == 2
    assert len(rows_of(run / "results.superseded.jsonl")) == 1
