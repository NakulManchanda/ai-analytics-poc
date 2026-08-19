from __future__ import annotations

from typing import Any

import pytest
from app.state import (
    Conversation,
    DuplicateEntityError,
    DynamoDBStateRepository,
    EntityNotFoundError,
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
from botocore.exceptions import ClientError


class FakeDynamoDBTable:
    """In-memory fake simulating DynamoDB Table API and condition expressions."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    def put_item(
        self,
        Item: dict[str, Any],
        ConditionExpression: str | None = None,
    ) -> dict[str, Any]:
        key = (Item["pk"], Item["sk"])

        if ConditionExpression == "attribute_not_exists(pk)":
            if any(k[0] == Item["pk"] for k in self.items):
                raise ClientError(
                    {"Error": {"Code": "ConditionalCheckFailedException"}},
                    "PutItem",
                )
        elif ConditionExpression == "attribute_not_exists(sk)":
            if key in self.items:
                raise ClientError(
                    {"Error": {"Code": "ConditionalCheckFailedException"}},
                    "PutItem",
                )
        elif ConditionExpression == "attribute_exists(pk)":
            if not any(k[0] == Item["pk"] for k in self.items):
                raise ClientError(
                    {"Error": {"Code": "ConditionalCheckFailedException"}},
                    "PutItem",
                )

        self.items[key] = dict(Item)
        return {}

    def get_item(self, Key: dict[str, Any]) -> dict[str, Any]:
        key = (Key["pk"], Key["sk"])
        item = self.items.get(key)
        if item is not None:
            return {"Item": dict(item)}
        return {}

    def query(
        self,
        KeyConditionExpression: Any = None,
        ScanIndexForward: bool = True,
        Limit: int | None = None,
    ) -> dict[str, Any]:
        pk_val = None
        sk_prefix = None

        def extract_conditions(expr: Any) -> None:
            nonlocal pk_val, sk_prefix
            if hasattr(expr, "_values"):
                if len(expr._values) == 2 and hasattr(expr._values[0], "name"):
                    key_attr, val = expr._values
                    if key_attr.name == "pk":
                        pk_val = val
                    elif key_attr.name == "sk":
                        sk_prefix = val
                else:
                    for v in expr._values:
                        extract_conditions(v)

        extract_conditions(KeyConditionExpression)

        matching = []
        for (pk, sk), item in self.items.items():
            if pk == pk_val:
                if sk_prefix is None or sk.startswith(sk_prefix):
                    matching.append(dict(item))

        matching.sort(key=lambda x: x["sk"], reverse=not ScanIndexForward)
        if Limit is not None and Limit > 0:
            matching = matching[:Limit]
        return {"Items": matching}


class FakeDynamoDBResource:
    def __init__(self, table: FakeDynamoDBTable) -> None:
        self._table = table

    def Table(self, name: str) -> FakeDynamoDBTable:
        return self._table


@pytest.fixture
def fake_dynamo() -> tuple[DynamoDBStateRepository, FakeDynamoDBTable]:
    table = FakeDynamoDBTable()
    resource = FakeDynamoDBResource(table)
    repo = DynamoDBStateRepository(
        table_name="test-application-state", dynamodb_resource=resource
    )
    return repo, table


def test_dynamodb_repository_conversation_crud(
    fake_dynamo: tuple[DynamoDBStateRepository, FakeDynamoDBTable],
) -> None:
    repo, table = fake_dynamo
    conv_id = generate_conversation_id()

    conv = Conversation(
        conversation_id=conv_id,
        title="Taxi analysis",
        metadata={"user_tier": "pilot", "query_count": 0},
    )
    created = repo.create_conversation(conv)
    assert created.conversation_id == conv_id

    # Verify single-table key shape in raw DynamoDB storage
    raw_item = table.items.get((f"CONV#{conv_id}", "METADATA"))
    assert raw_item is not None
    assert raw_item["pk"] == f"CONV#{conv_id}"
    assert raw_item["sk"] == "METADATA"
    assert raw_item["entity_type"] == "conversation"

    # Get conversation
    fetched = repo.get_conversation(conv_id)
    assert fetched is not None
    assert fetched.conversation_id == conv_id
    assert fetched.title == "Taxi analysis"
    assert fetched.metadata == {"user_tier": "pilot", "query_count": 0}

    # Duplicate creation rejected
    with pytest.raises(DuplicateEntityError):
        repo.create_conversation(conv)


def test_dynamodb_repository_messages_ordering(
    fake_dynamo: tuple[DynamoDBStateRepository, FakeDynamoDBTable],
) -> None:
    repo, table = fake_dynamo
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

    # list_messages returns strictly ordered by sequence
    messages = repo.list_messages(conv_id)
    assert len(messages) == 2
    assert messages[0].sequence == 1
    assert messages[0].content == "Which pickup zones have the most trips?"
    assert messages[1].sequence == 2

    # Message creation for missing conversation raises EntityNotFoundError
    with pytest.raises(EntityNotFoundError):
        repo.add_message(
            Message(
                message_id=generate_message_id(),
                conversation_id="non_existent_conv",
                sequence=1,
                role="user",
                content="hello",
            )
        )


def test_dynamodb_repository_run_and_steps_persistence(
    fake_dynamo: tuple[DynamoDBStateRepository, FakeDynamoDBTable],
) -> None:
    repo, table = fake_dynamo
    conv_id = generate_conversation_id()
    repo.create_conversation(Conversation(conversation_id=conv_id))

    run_id = generate_run_id()
    run = Run(
        run_id=run_id,
        conversation_id=conv_id,
        model="anthropic.claude",
        prompt_version="m7",
        input_tokens=100,
        output_tokens=50,
        estimated_cost_usd=0.0035,
    )
    repo.create_run(run)

    step1 = RunStep(
        step_id=generate_step_id(),
        run_id=run_id,
        sequence=1,
        step_type="llm_proposal",
        llm_call_id=generate_llm_call_id(),
        status="completed",
        duration_ms=320,
    )
    step2 = RunStep(
        step_id=generate_step_id(),
        run_id=run_id,
        sequence=2,
        step_type="tool_query",
        tool_name="query_taxi_data",
        tool_call_id=generate_tool_call_id(),
        query_id=generate_query_id(),
        status="completed",
        duration_ms=45,
    )

    repo.add_run_step(step1)
    repo.add_run_step(step2)

    fetched_run = repo.get_run(run_id)
    assert fetched_run is not None
    assert fetched_run.estimated_cost_usd == pytest.approx(0.0035)

    steps = repo.list_run_steps(run_id)
    assert len(steps) == 2
    assert steps[0].sequence == 1
    assert steps[0].llm_call_id is not None
    assert steps[1].sequence == 2
    assert steps[1].query_id is not None


def test_dynamodb_repository_process_restart_survival() -> None:
    """Test state survives process restart across independent repository instances."""
    shared_table = FakeDynamoDBTable()
    shared_resource = FakeDynamoDBResource(shared_table)

    # Process 1: Create conversation, message, run, step
    process1_repo = DynamoDBStateRepository(
        table_name="test-application-state", dynamodb_resource=shared_resource
    )
    conv_id = generate_conversation_id()
    process1_repo.create_conversation(Conversation(conversation_id=conv_id))

    msg1 = Message(
        message_id=generate_message_id(),
        conversation_id=conv_id,
        sequence=1,
        role="user",
        content="What hours have the highest trip volume?",
    )
    process1_repo.add_message(msg1)

    run_id = generate_run_id()
    process1_repo.create_run(
        Run(
            run_id=run_id,
            conversation_id=conv_id,
            status="in_progress",
        )
    )

    # Process 2 (simulated restart): completely new repository instance
    process2_repo = DynamoDBStateRepository(
        table_name="test-application-state", dynamodb_resource=shared_resource
    )

    # Verify conversation & history reconstructed
    reconstructed_conv = process2_repo.get_conversation(conv_id)
    assert reconstructed_conv is not None
    assert reconstructed_conv.conversation_id == conv_id

    messages = process2_repo.list_messages(conv_id)
    assert len(messages) == 1
    assert messages[0].content == "What hours have the highest trip volume?"

    # Continue the conversation in Process 2
    msg2 = Message(
        message_id=generate_message_id(),
        conversation_id=conv_id,
        sequence=2,
        role="assistant",
        content="Evening peak hours between 6 PM and 8 PM have the highest volume.",
    )
    process2_repo.add_message(msg2)

    updated_run = Run(
        run_id=run_id,
        conversation_id=conv_id,
        status="completed",
        input_tokens=200,
        output_tokens=80,
        estimated_cost_usd=0.002,
    )
    process2_repo.update_run(updated_run)

    # Verify state after Process 2 updates
    final_messages = process2_repo.list_messages(conv_id)
    assert len(final_messages) == 2
    assert final_messages[1].sequence == 2

    final_run = process2_repo.get_run(run_id)
    assert final_run is not None
    assert final_run.status == "completed"
    assert final_run.input_tokens == 200
