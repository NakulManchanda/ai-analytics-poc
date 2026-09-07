from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any

import pytest
from app.bedrock_budget import (
    NOVA_MICRO_MAX_CALL_RESERVATION_MICRO_USD,
    BedrockBudgetExceededError,
    BedrockBudgetUnavailableError,
    DynamoDBBedrockBudget,
)
from app.config import Settings
from app.llm import LLMProviderError, create_llm_client
from botocore.exceptions import ClientError


class AtomicBudgetTable:
    """Small DynamoDB conditional-update fake for the budget repository contract."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    def update_item(self, **kwargs: Any) -> dict[str, Any]:
        key = (kwargs["Key"]["pk"], kwargs["Key"]["sk"])
        values = kwargs["ExpressionAttributeValues"]
        amount = int(values[":amount"])
        remaining = int(values[":remaining"])
        current = int(self.items.get(key, {}).get("reserved_micro_usd", 0))
        if current > remaining:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem"
            )
        self.items[key] = {
            "pk": key[0],
            "sk": key[1],
            "reserved_micro_usd": Decimal(current + amount),
        }
        return {"Attributes": dict(self.items[key])}


class AtomicBudgetResource:
    def __init__(self, table: AtomicBudgetTable) -> None:
        self.table = table

    def Table(self, _name: str) -> AtomicBudgetTable:
        return self.table


def test_reservation_is_durable_and_uses_a_utc_month_key() -> None:
    table = AtomicBudgetTable()
    budget = DynamoDBBedrockBudget(
        "shared-state", monthly_limit_micro_usd=NOVA_MICRO_MAX_CALL_RESERVATION_MICRO_USD * 2,
        dynamodb_resource=AtomicBudgetResource(table),
    )

    budget.reserve_nova_micro_call("2026-09")

    assert table.items[("BUDGET#BEDROCK", "MONTH#2026-09")][
        "reserved_micro_usd"
    ] == NOVA_MICRO_MAX_CALL_RESERVATION_MICRO_USD


def test_reservation_rejects_exhausted_month_before_invocation() -> None:
    budget = DynamoDBBedrockBudget(
        "shared-state", monthly_limit_micro_usd=NOVA_MICRO_MAX_CALL_RESERVATION_MICRO_USD,
        dynamodb_resource=AtomicBudgetResource(AtomicBudgetTable()),
    )

    budget.reserve_nova_micro_call("2026-09")
    with pytest.raises(BedrockBudgetExceededError):
        budget.reserve_nova_micro_call("2026-09")


def test_reservations_are_atomic_under_concurrency() -> None:
    table = AtomicBudgetTable()
    budget = DynamoDBBedrockBudget(
        "shared-state", monthly_limit_micro_usd=NOVA_MICRO_MAX_CALL_RESERVATION_MICRO_USD * 3,
        dynamodb_resource=AtomicBudgetResource(table),
    )

    def reserve() -> bool:
        try:
            budget.reserve_nova_micro_call("2026-09")
            return True
        except BedrockBudgetExceededError:
            return False

    with ThreadPoolExecutor(max_workers=12) as executor:
        admitted = list(executor.map(lambda _: reserve(), range(12)))

    assert sum(admitted) == 3
    assert table.items[("BUDGET#BEDROCK", "MONTH#2026-09")][
        "reserved_micro_usd"
    ] == NOVA_MICRO_MAX_CALL_RESERVATION_MICRO_USD * 3


def test_new_utc_month_has_an_independent_allowance() -> None:
    budget = DynamoDBBedrockBudget(
        "shared-state", monthly_limit_micro_usd=NOVA_MICRO_MAX_CALL_RESERVATION_MICRO_USD,
        dynamodb_resource=AtomicBudgetResource(AtomicBudgetTable()),
    )

    budget.reserve_nova_micro_call("2026-09")
    budget.reserve_nova_micro_call("2026-10")


def test_storage_failures_fail_closed() -> None:
    class BrokenTable:
        def update_item(self, **_kwargs: Any) -> None:
            raise ClientError({"Error": {"Code": "InternalServerError"}}, "UpdateItem")

    budget = DynamoDBBedrockBudget(
        "shared-state", dynamodb_resource=AtomicBudgetResource(BrokenTable())  # type: ignore[arg-type]
    )

    with pytest.raises(BedrockBudgetUnavailableError):
        budget.reserve_nova_micro_call("2026-09")


def test_real_bedrock_client_refuses_to_start_without_a_durable_budget_store() -> None:
    with pytest.raises(ValueError, match="DYNAMODB_TABLE_NAME"):
        create_llm_client(Settings())


def test_real_bedrock_client_reserves_before_each_provider_invocation() -> None:
    calls: list[str] = []

    class RecordingBudget:
        def reserve(self, model_id: str) -> None:
            calls.append(model_id)

    class Runtime:
        def converse(self, **_request: Any) -> dict[str, Any]:
            return {
                "output": {"message": {"content": [{"text": "ok"}]}},
                "usage": {"inputTokens": 1, "outputTokens": 1},
                "metrics": {"latencyMs": 1},
            }

    client = create_llm_client(
        Settings(dynamodb_table_name="shared-state"),
        budget=RecordingBudget(),
        runtime_client=Runtime(),
    )

    assert client.ask("hello").text == "ok"
    assert calls == ["amazon.nova-micro-v1:0"]


def test_real_bedrock_client_does_not_invoke_provider_when_budget_is_exhausted() -> None:
    class ExhaustedBudget:
        def reserve(self, _model_id: str) -> None:
            raise BedrockBudgetExceededError()

    class Runtime:
        def converse(self, **_request: Any) -> dict[str, Any]:
            raise AssertionError("provider must not be invoked")

    client = create_llm_client(
        Settings(dynamodb_table_name="shared-state"),
        budget=ExhaustedBudget(),
        runtime_client=Runtime(),
    )

    with pytest.raises(LLMProviderError) as error:
        client.ask("hello")
    assert error.value.code == "bedrock_budget_exhausted"
    assert error.value.retryable is False
