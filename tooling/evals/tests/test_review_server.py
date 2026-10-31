"""Interactive review page + save endpoint (#190)."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from evals.review_server import make_server
from evals.reviews import load_reviews
from evals.run_review import render_review


@pytest.fixture
def srv(run):
    s = make_server(run, "alice")
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield run, s
    s.shutdown()
    s.server_close()


def call(s, path="/", body=None, headers=None):
    h = {"Host": f"127.0.0.1:{s.server_port}", **(headers or {})}
    req = urllib.request.Request(
        f"http://127.0.0.1:{s.server_port}{path}",
        data=None if body is None else json.dumps(body).encode(),
        headers=h,
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def item(**kw):
    return {
        "case_id": "c1",
        "turn_index": 0,
        "dimension": "tool",
        "verdict": "incorrect",
        **kw,
    }


def test_page_has_form_and_disagreement_filters(srv):
    run, s = srv
    code, html = call(s)
    assert code == 200 and 'class="rv"' in html and "human review" in html
    assert 'data-m="incomplete"' in html and "incomplete" in html
    assert "human review" not in render_review(run)  # static report unchanged


def test_save_appends_verdict_and_page_shows_disagreement(srv):
    run, s = srv
    code, body = call(s, "/api/review", {"items": [item(notes="wrong tool")]})
    assert code == 200 and json.loads(body) == {"saved": 1}
    rec = load_reviews(run).resolved[("c1", 0, "tool")]
    assert rec["reviewer"] == "alice" and rec["notes"] == "wrong tool"
    _, html = call(s)
    assert "grader: correct" in html and "disagree" in html and "selected" in html


def test_bad_batch_writes_nothing(srv):
    run, s = srv
    code, _ = call(s, "/api/review", {"items": [item(), item(verdict="maybe")]})
    assert code == 400 and not (run / "reviews.jsonl").exists()
    assert call(s, "/api/review", {"items": [item(case_id="zz")]})[0] == 400
    assert call(s, "/api/review", {"items": []})[0] == 400
    assert call(s, "/api/review", {})[0] == 400


def test_rejects_foreign_host_and_origin(srv):
    run, s = srv
    assert call(s, headers={"Host": "evil.example"})[0] == 404
    assert (
        call(s, "/api/review", {"items": [item()]}, {"Origin": "http://evil.example"})[
            0
        ]
        == 404
    )
    assert not (run / "reviews.jsonl").exists()


def test_batch_with_unknown_second_turn_writes_nothing(srv):
    run, s = srv
    code, _ = call(s, "/api/review", {"items": [item(), item(case_id="zz")]})
    assert code == 400 and not (run / "reviews.jsonl").exists()
    call(s, "/api/review", {"items": [item(verdict="correct")]})
    before = (run / "reviews.jsonl").read_bytes()
    code, _ = call(s, "/api/review", {"items": [item(), item(turn_index=99)]})
    assert code == 400 and (run / "reviews.jsonl").read_bytes() == before


def test_notes_only_rereview_and_history_visible(srv):
    run, s = srv
    call(s, "/api/review", {"items": [item(verdict="incorrect", notes="first")]})
    # same verdict, new notes (what the page submits when only the notes changed)
    call(s, "/api/review", {"items": [item(verdict="incorrect", notes="second")]})
    rec = load_reviews(run).resolved[("c1", 0, "tool")]
    assert rec["notes"] == "second" and len(rec["superseded"]) == 1
    _, html = call(s)
    assert 'value="second"' in html and 'data-cur="second"' in html
    assert "history (2)" in html and "first" in html and "alice" in html
    assert "reviewed_at" not in html and "Z</span>" in html
