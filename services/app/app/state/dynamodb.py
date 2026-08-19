from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from app.state.models import Conversation, Message, Run, RunStep
from app.state.repository import (
    ConcurrencyError,
    DuplicateEntityError,
    EntityNotFoundError,
    StateError,
)

DEFAULT_TABLE_NAME = "ai-analytics-poc-dev-application-state"


def _to_decimal(val: Any) -> Any:
    """Convert floats to Decimal for DynamoDB serialization."""
    if isinstance(val, float):
        return Decimal(str(val))
    if isinstance(val, dict):
        return {k: _to_decimal(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_to_decimal(v) for v in val]
    return val


def _from_decimal(val: Any) -> Any:
    """Convert Decimals back to standard Python types."""
    if isinstance(val, Decimal):
        return float(val) if "." in str(val) else int(val)
    if isinstance(val, dict):
        return {k: _from_decimal(v) for k, v in val.items()}
    if isinstance(val, list):
        return [_from_decimal(v) for v in val]
    return val


class DynamoDBStateRepository:
    """Durable state repository backed by a single DynamoDB table."""

    def __init__(
        self,
        table_name: str | None = None,
        dynamodb_resource: Any = None,
    ) -> None:
        self._table_name = (
            table_name
            or os.environ.get("DYNAMODB_TABLE_NAME")
            or os.environ.get("STATE_TABLE_NAME")
            or DEFAULT_TABLE_NAME
        )
        self._dynamodb = dynamodb_resource or boto3.resource("dynamodb")
        self._table = self._dynamodb.Table(self._table_name)

    def create_conversation(self, conversation: Conversation) -> Conversation:
        item = {
            "pk": f"CONV#{conversation.conversation_id}",
            "sk": "METADATA",
            "entity_type": "conversation",
            "conversation_id": conversation.conversation_id,
            "created_at": conversation.created_at,
            "updated_at": conversation.updated_at,
            "title": conversation.title,
            "metadata": _to_decimal(conversation.metadata),
        }
        item = {k: v for k, v in item.items() if v is not None}
        try:
            self._table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(pk)",
            )
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            if code == "ConditionalCheckFailedException":
                raise DuplicateEntityError(
                    f"Conversation {conversation.conversation_id} already exists"
                ) from error
            raise StateError(f"Failed to create conversation: {error}") from error
        return conversation

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        try:
            response = self._table.get_item(
                Key={"pk": f"CONV#{conversation_id}", "sk": "METADATA"}
            )
        except ClientError as error:
            raise StateError(f"Failed to get conversation: {error}") from error

        item = response.get("Item")
        if not item:
            return None
        return Conversation(
            conversation_id=item["conversation_id"],
            created_at=item.get("created_at", ""),
            updated_at=item.get("updated_at", ""),
            title=item.get("title"),
            metadata=_from_decimal(item.get("metadata", {})),
        )

    def add_message(self, message: Message) -> Message:
        # Verify conversation exists first
        conv = self.get_conversation(message.conversation_id)
        if conv is None:
            raise EntityNotFoundError(
                f"Conversation {message.conversation_id} not found"
            )

        sk = f"MSG#{message.sequence:06d}#{message.message_id}"
        item = {
            "pk": f"CONV#{message.conversation_id}",
            "sk": sk,
            "entity_type": "message",
            "message_id": message.message_id,
            "conversation_id": message.conversation_id,
            "sequence": message.sequence,
            "role": message.role,
            "content": message.content,
            "created_at": message.created_at,
            "metadata": _to_decimal(message.metadata),
        }
        item = {k: v for k, v in item.items() if v is not None}
        try:
            self._table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(sk)",
            )
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            if code == "ConditionalCheckFailedException":
                raise DuplicateEntityError(
                    f"Message {message.message_id} already exists"
                ) from error
            raise StateError(f"Failed to add message: {error}") from error
        return message

    def list_messages(
        self, conversation_id: str, limit: int | None = None
    ) -> list[Message]:
        try:
            kwargs: dict[str, Any] = {
                "KeyConditionExpression": (
                    Key("pk").eq(f"CONV#{conversation_id}")
                    & Key("sk").begins_with("MSG#")
                ),
                "ScanIndexForward": True,
            }
            if limit is not None and limit > 0:
                kwargs["Limit"] = limit
            response = self._table.query(**kwargs)
        except ClientError as error:
            raise StateError(f"Failed to list messages: {error}") from error

        items = response.get("Items", [])
        messages = [
            Message(
                message_id=item["message_id"],
                conversation_id=item["conversation_id"],
                sequence=int(item["sequence"]),
                role=item["role"],
                content=item["content"],
                created_at=item.get("created_at", ""),
                metadata=_from_decimal(item.get("metadata", {})),
            )
            for item in items
        ]
        return messages

    def create_run(self, run: Run) -> Run:
        # Verify conversation exists
        conv = self.get_conversation(run.conversation_id)
        if conv is None:
            raise EntityNotFoundError(f"Conversation {run.conversation_id} not found")

        item = {
            "pk": f"RUN#{run.run_id}",
            "sk": "METADATA",
            "entity_type": "run",
            "run_id": run.run_id,
            "conversation_id": run.conversation_id,
            "message_id": run.message_id,
            "status": run.status,
            "model": run.model,
            "prompt_version": run.prompt_version,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
            "input_tokens": run.input_tokens,
            "output_tokens": run.output_tokens,
            "estimated_cost_usd": _to_decimal(run.estimated_cost_usd),
            "failure_code": run.failure_code,
            "metadata": _to_decimal(run.metadata),
        }
        item = {k: v for k, v in item.items() if v is not None}
        try:
            self._table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(pk)",
            )
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            if code == "ConditionalCheckFailedException":
                raise DuplicateEntityError(
                    f"Run {run.run_id} already exists"
                ) from error
            raise StateError(f"Failed to create run: {error}") from error
        return run

    def get_run(self, run_id: str) -> Run | None:
        try:
            response = self._table.get_item(
                Key={"pk": f"RUN#{run_id}", "sk": "METADATA"}
            )
        except ClientError as error:
            raise StateError(f"Failed to get run: {error}") from error

        item = response.get("Item")
        if not item:
            return None
        return Run(
            run_id=item["run_id"],
            conversation_id=item["conversation_id"],
            message_id=item.get("message_id"),
            status=item.get("status", "in_progress"),
            model=item.get("model"),
            prompt_version=item.get("prompt_version"),
            started_at=item.get("started_at", ""),
            completed_at=item.get("completed_at"),
            input_tokens=int(item.get("input_tokens", 0)),
            output_tokens=int(item.get("output_tokens", 0)),
            estimated_cost_usd=float(item.get("estimated_cost_usd", 0.0)),
            failure_code=item.get("failure_code"),
            metadata=_from_decimal(item.get("metadata", {})),
        )

    def update_run(self, run: Run) -> Run:
        item = {
            "pk": f"RUN#{run.run_id}",
            "sk": "METADATA",
            "entity_type": "run",
            "run_id": run.run_id,
            "conversation_id": run.conversation_id,
            "message_id": run.message_id,
            "status": run.status,
            "model": run.model,
            "prompt_version": run.prompt_version,
            "started_at": run.started_at,
            "completed_at": run.completed_at,
            "input_tokens": run.input_tokens,
            "output_tokens": run.output_tokens,
            "estimated_cost_usd": _to_decimal(run.estimated_cost_usd),
            "failure_code": run.failure_code,
            "metadata": _to_decimal(run.metadata),
        }
        item = {k: v for k, v in item.items() if v is not None}
        try:
            self._table.put_item(
                Item=item,
                ConditionExpression="attribute_exists(pk)",
            )
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            if code == "ConditionalCheckFailedException":
                raise EntityNotFoundError(f"Run {run.run_id} not found") from error
            raise ConcurrencyError(f"Failed to update run: {error}") from error
        return run

    def add_run_step(self, step: RunStep) -> RunStep:
        run = self.get_run(step.run_id)
        if run is None:
            raise EntityNotFoundError(f"Run {step.run_id} not found")

        sk = f"STEP#{step.sequence:06d}#{step.step_id}"
        item = {
            "pk": f"RUN#{step.run_id}",
            "sk": sk,
            "entity_type": "run_step",
            "step_id": step.step_id,
            "run_id": step.run_id,
            "sequence": step.sequence,
            "step_type": step.step_type,
            "status": step.status,
            "tool_name": step.tool_name,
            "llm_call_id": step.llm_call_id,
            "tool_call_id": step.tool_call_id,
            "query_id": step.query_id,
            "input_summary": step.input_summary,
            "output_summary": step.output_summary,
            "started_at": step.started_at,
            "completed_at": step.completed_at,
            "duration_ms": step.duration_ms,
            "metadata": _to_decimal(step.metadata),
        }
        item = {k: v for k, v in item.items() if v is not None}
        try:
            self._table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(sk)",
            )
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code")
            if code == "ConditionalCheckFailedException":
                raise DuplicateEntityError(
                    f"Step {step.step_id} already exists"
                ) from error
            raise StateError(f"Failed to add run step: {error}") from error
        return step

    def list_run_steps(self, run_id: str) -> list[RunStep]:
        try:
            response = self._table.query(
                KeyConditionExpression=(
                    Key("pk").eq(f"RUN#{run_id}") & Key("sk").begins_with("STEP#")
                ),
                ScanIndexForward=True,
            )
        except ClientError as error:
            raise StateError(f"Failed to list run steps: {error}") from error

        items = response.get("Items", [])
        steps = [
            RunStep(
                step_id=item["step_id"],
                run_id=item["run_id"],
                sequence=int(item["sequence"]),
                step_type=item["step_type"],
                status=item.get("status", "in_progress"),
                tool_name=item.get("tool_name"),
                llm_call_id=item.get("llm_call_id"),
                tool_call_id=item.get("tool_call_id"),
                query_id=item.get("query_id"),
                input_summary=item.get("input_summary"),
                output_summary=item.get("output_summary"),
                started_at=item.get("started_at", ""),
                completed_at=item.get("completed_at"),
                duration_ms=int(item["duration_ms"]) if "duration_ms" in item else None,
                metadata=_from_decimal(item.get("metadata", {})),
            )
            for item in items
        ]
        return steps
