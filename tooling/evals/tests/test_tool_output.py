import json

from evals.traces import (
    ToolOutput,
    ToolUse,
    TurnView,
    attach_outputs,
    load_tool_outputs,
)


def _event(run_id: str, messages: list[dict]) -> str:
    event = {
        "event_type": "context.reduced",
        "run_id": run_id,
        "payload": {"working_context": {"recent_messages": messages}},
    }
    return f"id: evt_1\nevent: context.reduced\ndata: {json.dumps(event)}\n\n"


def _tool_msg(query_id: str, rows: list, truncated: bool) -> dict:
    content = {
        "columns": ["pickup_zone", "trip_count"],
        "query_id": query_id,
        "row_count": len(rows),
        "rows": rows,
        "truncated": truncated,
    }
    return {"role": "tool", "content": json.dumps(content)}


def test_load_tool_outputs_parses_rows_and_skips_text(tmp_path):
    msgs = [
        {"role": "user", "content": "Which zone has the most trips?"},
        _tool_msg("query_a", [["JFK Airport", 145240]], True),
        {"role": "assistant", "content": "{not json at all"},
    ]
    (tmp_path / "run_1.txt").write_text(
        'event: run.received\ndata: {"event_type": "run.received"}\n\n'
        + _event("run_1", msgs)
        + _event("run_1", msgs)  # repeated context snapshot: deduplicated
    )
    got = load_tool_outputs(tmp_path)
    assert list(got) == ["run_1"]
    assert got["run_1"] == [
        ToolOutput(
            columns=["pickup_zone", "trip_count"],
            rows=[["JFK Airport", 145240]],
            truncated=True,
            row_count=1,
            query_id="query_a",
            raw=got["run_1"][0].raw,
        )
    ]


def test_describe_style_output_without_rows_is_kept(tmp_path):
    content = json.dumps({"query_id": "query_d", "row_count": 7, "min": "2024-01-01"})
    (tmp_path / "run_2.txt").write_text(
        _event("run_2", [{"role": "tool", "content": content}])
    )
    out = load_tool_outputs(tmp_path)["run_2"][0]
    assert out.rows == [] and out.truncated is None and out.row_count == 7


def test_attach_outputs_by_query_id_and_missing_stays_none():
    view = TurnView("run_1", "c", 0)
    view.tools = [
        ToolUse("t", {}, "completed", "query_a", 1),
        ToolUse("t", {}, "completed", "query_b", 1),
    ]
    out = ToolOutput(["c"], [[1]], False, 1, "query_a")
    attach_outputs(view, [out])
    assert view.tools[0].output is out
    assert view.tools[1].output is None


def test_missing_events_dir_files_yield_nothing(tmp_path):
    assert load_tool_outputs(tmp_path) == {}
