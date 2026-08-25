from __future__ import annotations

from app.orchestration import (
    BudgetTracker,
    ContextReducer,
    ExecutionBudgets,
    sanitize_and_preview_tool_result,
)
from app.state import Message, generate_message_id


def test_context_reducer_keeps_current_request_verbatim() -> None:
    reducer = ContextReducer(recent_turns_window=2)
    prompt = "Which pickup zones have the highest fare amount in Brooklyn?"

    stored_messages = [
        Message(
            message_id=generate_message_id(),
            conversation_id="conv_1",
            sequence=1,
            role="user",
            content=prompt,
        )
    ]

    working_context = reducer.reduce(
        current_prompt=prompt,
        stored_messages=stored_messages,
        dataset_schema={
            "dataset": "nyc-taxi",
            "columns": ["PULocationID", "fare_amount"],
        },
    )

    assert working_context.current_user_message == prompt
    assert working_context.conversation_summary is None
    assert working_context.stored_message_count == 1
    assert working_context.included_message_count == 1
    assert working_context.schema_size_bytes > 0


def test_context_reducer_multi_turn_divergence_and_summarization() -> None:
    reducer = ContextReducer(
        recent_turns_window=2
    )  # Max 4 historical messages (2 turns)
    conv_id = "conv_multi_turn"

    # Simulate 3 turns stored in durable state (6 messages)
    stored_messages = [
        Message(
            message_id="m1",
            conversation_id=conv_id,
            sequence=1,
            role="user",
            content="What dataset is this?",
        ),
        Message(
            message_id="m2",
            conversation_id=conv_id,
            sequence=2,
            role="assistant",
            content="This is the NYC TLC yellow taxi dataset for 2024-01.",
        ),
        Message(
            message_id="m3",
            conversation_id=conv_id,
            sequence=3,
            role="user",
            content="Which pickup zones have the most trips?",
        ),
        Message(
            message_id="m4",
            conversation_id=conv_id,
            sequence=4,
            role="assistant",
            content="JFK Airport has the most trips with 1,500 pickups.",
        ),
        Message(
            message_id="m5",
            conversation_id=conv_id,
            sequence=5,
            role="user",
            content="What about average trip distance for JFK?",
        ),
        Message(
            message_id="m6",
            conversation_id=conv_id,
            sequence=6,
            role="assistant",
            content="Average distance from JFK is 14.2 miles.",
        ),
    ]

    current_prompt = "Compare JFK and LaGuardia fares."
    working_context = reducer.reduce(
        current_prompt=current_prompt,
        stored_messages=stored_messages,
        dataset_schema={"columns": ["PULocationID", "fare_amount", "trip_distance"]},
    )

    # Durable conversation != current LLM context
    assert working_context.stored_message_count == 6
    # Included: 4 recent messages (turns 2 & 3) + 1 current prompt = 5
    assert working_context.included_message_count == 5
    assert (
        working_context.stored_message_count != working_context.included_message_count
    )

    # Turn 1 should be summarized
    assert working_context.conversation_summary is not None
    assert "user: What dataset is this?" in working_context.conversation_summary
    assert (
        "assistant: This is the NYC TLC yellow taxi dataset"
        in working_context.conversation_summary
    )

    # Recent messages should contain m3, m4, m5, m6
    recent_ids = [m["message_id"] for m in working_context.recent_messages]
    assert recent_ids == ["m3", "m4", "m5", "m6"]


def test_context_reducer_uses_the_persisted_current_message_identity() -> None:
    reducer = ContextReducer(recent_turns_window=1)
    stored_messages = [
        Message("m1", "conv_1", 1, "user", "repeat this request"),
        Message("m2", "conv_1", 2, "assistant", "first answer"),
        Message("m3", "conv_1", 3, "user", "repeat this request"),
        Message("m4", "conv_1", 4, "assistant", "second answer"),
        Message("m5", "conv_1", 5, "user", "repeat this request"),
    ]

    working_context = reducer.reduce(
        current_prompt="repeat this request",
        current_message_id="m5",
        stored_messages=stored_messages,
    )

    assert working_context.current_user_message == "repeat this request"
    assert working_context.stored_message_count == 5
    assert working_context.included_message_count == 3
    assert working_context.conversation_summary is not None
    assert "user: repeat this request | assistant: first answer" == (
        working_context.conversation_summary
    )


def test_sanitize_and_preview_tool_result_replaces_large_results() -> None:
    large_rows = [[f"Zone_{i}", i * 100, f"Detail_{i}"] for i in range(100)]
    tool_result = {
        "columns": ["zone", "trips", "details"],
        "rows": large_rows,
        "row_count": 100,
        "query_id": "query_large_123",
        "execution_duration_ms": 45,
    }

    reduced = sanitize_and_preview_tool_result(tool_result, max_rows=3)

    assert reduced["query_id"] == "query_large_123"
    assert reduced["row_count"] == 100
    assert len(reduced["preview_rows"]) == 3
    assert reduced["preview_rows"] == [
        ["Zone_0", 0, "Detail_0"],
        ["Zone_1", 100, "Detail_1"],
        ["Zone_2", 200, "Detail_2"],
    ]
    assert reduced["artifact_ref"] == "artifact://nyc-taxi/queries/query_large_123"


def test_context_reducer_computes_remaining_budgets() -> None:
    budgets = ExecutionBudgets(
        max_iterations=6,
        max_tool_calls=8,
        max_llm_calls=6,
        max_input_tokens=30000,
        max_estimated_cost_usd=0.10,
    )
    tracker = BudgetTracker(budgets=budgets)
    tracker.record_iteration()
    tracker.record_llm_call(
        input_tokens=1500, output_tokens=200, estimated_cost_usd=0.0075
    )
    tracker.record_tool_call(result_bytes=512)

    reducer = ContextReducer()
    working_context = reducer.reduce(
        current_prompt="Tell me total trips.",
        stored_messages=[],
        budget_tracker=tracker,
        budgets=budgets,
    )

    rem = working_context.remaining_budget
    assert rem["current_iteration"] == 1
    assert rem["remaining_iterations"] == 5
    assert rem["remaining_tool_calls"] == 7
    assert rem["remaining_llm_calls"] == 5
    assert rem["remaining_input_tokens"] == 28500
    assert rem["remaining_estimated_cost_usd"] < 0.10
