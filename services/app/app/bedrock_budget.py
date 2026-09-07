from __future__ import annotations

from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.config import DEFAULT_MODEL_ID

DEFAULT_MONTHLY_BEDROCK_LIMIT_USD = "5.00"
MICRO_USD_PER_USD = 1_000_000
NOVA_MICRO_MAX_INPUT_TOKENS = 128_000
NOVA_MICRO_MAX_OUTPUT_TOKENS = 128
NOVA_MICRO_INPUT_USD_PER_MILLION_TOKENS = Decimal("0.035")
NOVA_MICRO_OUTPUT_USD_PER_MILLION_TOKENS = Decimal("0.140")
# Conservatively prices a 128K-token input plus the application's 128-token
# output ceiling at the pinned model's on-demand rates, rounded upward.
NOVA_MICRO_MAX_CALL_RESERVATION_MICRO_USD = int(
    (
        (
            NOVA_MICRO_MAX_INPUT_TOKENS * NOVA_MICRO_INPUT_USD_PER_MILLION_TOKENS
            + NOVA_MICRO_MAX_OUTPUT_TOKENS
            * NOVA_MICRO_OUTPUT_USD_PER_MILLION_TOKENS
        )
        * MICRO_USD_PER_USD
        / 1_000_000
    ).to_integral_value(rounding=ROUND_CEILING)
)


class BedrockBudgetError(Exception):
    """Base class for failures authorizing a paid Bedrock invocation."""


class BedrockBudgetExceededError(BedrockBudgetError):
    """Raised when the shared monthly allowance has no remaining reservation."""


class BedrockBudgetUnavailableError(BedrockBudgetError):
    """Raised when durable budget state cannot safely authorize an invocation."""


def utc_month(now: datetime | None = None) -> str:
    value = now or datetime.now(UTC)
    return value.astimezone(UTC).strftime("%Y-%m")


def monthly_limit_micro_usd(value: str = DEFAULT_MONTHLY_BEDROCK_LIMIT_USD) -> int:
    try:
        amount = Decimal(value)
    except Exception as error:
        raise ValueError(
            "GLOBAL_BEDROCK_MONTHLY_LIMIT_USD must be a positive USD amount"
        ) from error
    micro_usd = amount * MICRO_USD_PER_USD
    if amount <= 0 or micro_usd != micro_usd.to_integral_value():
        raise ValueError("GLOBAL_BEDROCK_MONTHLY_LIMIT_USD must be a positive USD amount")
    return int(micro_usd)


class DynamoDBBedrockBudget:
    """Atomically reserves conservative Nova Micro spend in the shared state table."""

    def __init__(
        self,
        table_name: str,
        *,
        monthly_limit_micro_usd: int = monthly_limit_micro_usd(),
        dynamodb_resource: Any = None,
        region_name: str | None = None,
    ) -> None:
        if not table_name:
            raise ValueError("A durable DynamoDB table is required for Bedrock budget enforcement")
        if monthly_limit_micro_usd < NOVA_MICRO_MAX_CALL_RESERVATION_MICRO_USD:
            raise ValueError(
                "Monthly Bedrock allowance cannot fund one conservative Nova Micro call"
            )
        self._monthly_limit_micro_usd = monthly_limit_micro_usd
        resource = dynamodb_resource or boto3.resource("dynamodb", region_name=region_name)
        self._table = resource.Table(table_name)

    def reserve_nova_micro_call(self, month: str | None = None) -> None:
        reservation_month = month or utc_month()
        try:
            self._table.update_item(
                Key={"pk": "BUDGET#BEDROCK", "sk": f"MONTH#{reservation_month}"},
                UpdateExpression=(
                    "SET reserved_micro_usd = "
                    "if_not_exists(reserved_micro_usd, :zero) + :amount"
                ),
                ConditionExpression=(
                    "attribute_not_exists(reserved_micro_usd) OR "
                    "reserved_micro_usd <= :remaining"
                ),
                ExpressionAttributeValues={
                    ":zero": Decimal(0),
                    ":amount": Decimal(NOVA_MICRO_MAX_CALL_RESERVATION_MICRO_USD),
                    ":remaining": Decimal(
                        self._monthly_limit_micro_usd
                        - NOVA_MICRO_MAX_CALL_RESERVATION_MICRO_USD
                    ),
                },
            )
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise BedrockBudgetExceededError(
                    "The shared monthly Bedrock allowance is exhausted"
                ) from error
            raise BedrockBudgetUnavailableError(
                "The shared Bedrock allowance could not be checked"
            ) from error
        except BotoCoreError as error:
            raise BedrockBudgetUnavailableError(
                "The shared Bedrock allowance could not be checked"
            ) from error

    def reserve(self, model_id: str) -> None:
        if model_id != DEFAULT_MODEL_ID:
            raise BedrockBudgetUnavailableError(
                "No safe shared allowance pricing is configured for this Bedrock model"
            )
        self.reserve_nova_micro_call()
