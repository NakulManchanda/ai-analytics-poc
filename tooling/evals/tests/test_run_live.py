"""run-live against a local stdlib stub server; no real network."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from evals.live_run import run_live
from evals.runs import RunError

SEEN: list[dict] = []


class Stub(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        SEEN.append(body)
        n = len(SEEN)
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
        out = b"event: run.started\ndata: {}\n\nevent: run.completed\ndata: {}\n\n"
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


@pytest.fixture
def stub():
    SEEN.clear()
    srv = HTTPServer(("127.0.0.1", 0), Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


@pytest.fixture
def cases(tmp_path):
    p = tmp_path / "cases.jsonl"
    p.write_text(
        json.dumps({"id": "c-1", "turns": [{"question": "q1"}, {"question": "q2"}]})
        + "\n"
    )
    return p


def mk(tmp_path):
    run = tmp_path / "r-20200101T000000Z"
    run.mkdir()
    (run / "manifest.json").write_text("{}")
    return run


def test_live_writes_results(tmp_path, stub, cases):
    run = mk(tmp_path)
    run_live(run, run.name, stub, ["c-1"], False, False, 5, cases)
    rows = [json.loads(x) for x in (run / "results.jsonl").read_text().splitlines()]
    assert [r["turn_index"] for r in rows] == [0, 1]
    assert set(rows[0]) == {
        "case_id",
        "turn_index",
        "question",
        "conversation_id",
        "run_id",
        "terminal",
        "wall_s",
    }
    assert rows[1]["conversation_id"] == rows[0]["conversation_id"]
    assert rows[0]["terminal"] == "run.completed"
    assert (run / "events" / "run_1.txt").is_file()
    assert len(json.loads((run / "matches.json").read_text())["matches"]) == 2
    assert {b["eval_run_id"] for b in SEEN} == {run.name}
    with pytest.raises(RunError):
        run_live(run, run.name, stub, ["c-1"], False, False, 5, cases)


def test_smoke_isolated(tmp_path, stub, cases):
    run = mk(tmp_path)
    run_live(run, run.name, stub, [], True, True, 5, cases)
    assert not (run / "results.jsonl").exists()
    assert (run / "smoke" / "results.jsonl").is_file()
    assert {b["eval_run_id"] for b in SEEN} == {run.name + "-smoke"}


def test_requires_manifest(tmp_path, cases):
    run = tmp_path / "x"
    run.mkdir()
    with pytest.raises(RunError):
        run_live(run, "x", "http://127.0.0.1:1", ["c-1"], False, False, 1, cases)
