from __future__ import annotations

import pytest
from app.state import (
    Conversation,
    DuplicateEntityError,
    EntityNotFoundError,
    InMemoryStateRepository,
    Message,
    Run,
    RunStep,
    generate_conversation_id,
    generate_llm_call_id,
    generate_message_id,
    generate_query_id,
    generate_run_id,
    generate_step_id,
    generate_tool_call_id,
)


def test_id_generators_produce_distinct_prefixed_identifiers() -> None:
    conv_id = generate_conversation_id()
    msg_id = generate_message_id()
    run_id = generate_run_id()
    step_id = generate_step_id()
    llm_id = generate_llm_call_id()
    tool_id = generate_tool_call_id()
    qry_id = generate_query_id()

    assert conv_id.startswith("conv_")
    assert msg_id.startswith("msg_")
    assert run_id.startswith("run_")
    assert step_id.startswith("step_")
    assert llm_id.startswith("call_")
    assert tool_id.startswith("tcall_")
    assert qry_id.startswith("qry_")

    all_ids = {conv_id, msg_id, run_id, step_id, llm_id, tool_id, qry_id}
    assert len(all_ids) == 7


def test_in_memory_repository_conversation_lifecycle() -> None:
    repo = InMemoryStateRepository()
    conv_id = generate_conversation_id()

    conv = Conversation(conversation_id=conv_id, title="Test Conversation")
    created = repo.create_conversation(conv)
    assert created.conversation_id == conv_id
    assert created.title == "Test Conversation"

    retrieved = repo.get_conversation(conv_id)
    assert retrieved is not None
    assert retrieved.conversation_id == conv_id

    assert repo.get_conversation("non_existent") is None

    with pytest.raises(DuplicateEntityError):
        repo.create_conversation(conv)


def test_in_memory_repository_messages_ordering_and_pagination() -> None:
    repo = InMemoryStateRepository()
    conv_id = generate_conversation_id()
    repo.create_conversation(Conversation(conversation_id=conv_id))

    msg1 = Message(
        message_id=generate_message_id(),
        conversation_id=conv_id,
        sequence=1,
        role="user",
        content="Which pickup zones have the most trips?",
    )
    msg2 = Message(
        message_id=generate_message_id(),
        conversation_id=conv_id,
        sequence=2,
        role="assistant",
        content="The top pickup zone is JFK Airport.",
    )

    repo.add_message(msg2)
    repo.add_message(msg1)

    messages = repo.list_messages(conv_id)
    assert len(messages) == 2
    assert [m.sequence for m in messages] == [1, 2]
    assert messages[0].content == "Which pickup zones have the most trips?"

    limited = repo.list_messages(conv_id, limit=1)
    assert len(limited) == 1
    assert limited[0].sequence == 1

    with pytest.raises(EntityNotFoundError):
        repo.add_message(
            Message(
                message_id=generate_message_id(),
                conversation_id="invalid_conv",
                sequence=1,
                role="user",
                content="hello",
            )
        )


def test_in_memory_repository_run_and_step_lifecycle() -> None:
    repo = InMemoryStateRepository()
    conv_id = generate_conversation_id()
    repo.create_conversation(Conversation(conversation_id=conv_id))

    run_id = generate_run_id()
    run = Run(
        run_id=run_id,
        conversation_id=conv_id,
        model="us.anthropic.claude-3-5-sonnet-20241022-v2:0",
        prompt_version="m7.v1",
    )
    repo.create_run(run)

    step1 = RunStep(
        step_id=generate_step_id(),
        run_id=run_id,
        sequence=1,
        step_type="llm_call",
        llm_call_id=generate_llm_call_id(),
        status="completed",
        input_summary="propose query",
        output_summary="top_pickup_zones",
        duration_ms=450,
    )
    step2 = RunStep(
        step_id=generate_step_id(),
        run_id=run_id,
        sequence=2,
        step_type="tool_call",
        tool_name="query_taxi_data",
        tool_call_id=generate_tool_call_id(),
        query_id=generate_query_id(),
        status="completed",
        duration_ms=25,
    )

    repo.add_run_step(step2)
    repo.add_run_step(step1)

    steps = repo.list_run_steps(run_id)
    assert len(steps) == 2
    assert [s.sequence for s in steps] == [1, 2]
    assert steps[0].step_type == "llm_call"
    assert steps[1].step_type == "tool_call"

    updated_run = Run(
        run_id=run_id,
        conversation_id=conv_id,
        status="completed",
        input_tokens=150,
        output_tokens=60,
        estimated_cost_usd=0.0012,
    )
    repo.update_run(updated_run)

    fetched_run = repo.get_run(run_id)
    assert fetched_run is not None
    assert fetched_run.status == "completed"
    assert fetched_run.input_tokens == 150
    assert fetched_run.estimated_cost_usd == 0.0012
