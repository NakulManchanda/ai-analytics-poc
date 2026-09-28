"""Prefix Token Contract module for the taxi analytics application.

Differentiates prompt regions according to Section 5 of docs/inference-project-plan.md:
1. Globally shared: system prompt, dataset rules/instructions, tool and MCP schemas.
2. Conversation-shared: prior turns, previous tool calls, DuckDB observations.
3. Unique suffix: newest user/tool suffix, latest question, generation target.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# Standard globally shared system instructions for NYC taxi analytics
DEFAULT_SYSTEM_PROMPT = (
    "You are the NYC Taxi Analytics AI Assistant. You have access to governed "
    "read-only analytics tools over the NYC yellow taxi dataset. Always base answers "
    "strictly on governed query observations and dataset profiles."
)

DEFAULT_TAXI_RULES = (
    "Dataset constraints: All queries run against the pinned NYC Taxi trip dataset. "
    "Analyses are pre-governed: 'top_pickup_zones', 'trip_volume_by_hour', "
    "'average_distance_by_weekday', and 'average_trip_metrics'."
)


class PromptRegion(StrEnum):
    GLOBAL_SHARED = "global_shared"
    CONVERSATION_SHARED = "conversation_shared"
    UNIQUE_SUFFIX = "unique_suffix"


@dataclass(frozen=True)
class PrefixPartition:
    """Explicit token breakdown of a prompt across reuse boundaries."""

    global_shared: str
    conversation_shared: str
    unique_suffix: str
    prefix_id: str
    estimated_total_tokens: int
    estimated_shared_tokens: int

    @property
    def full_prompt(self) -> str:
        parts = [
            p
            for p in (self.global_shared, self.conversation_shared, self.unique_suffix)
            if p
        ]
        return "\n\n".join(parts)


def estimate_tokens(text: str) -> int:
    """Deterministic token estimation using 1.33x word count with minimum bound."""
    if not text:
        return 0
    words = text.split()
    if not words:
        return 0
    return max(1, int(len(words) * 1.33))


def compute_prefix_id(global_shared: str, conversation_shared: str) -> str:
    """Compute deterministic 16-character hex hash identifying the reusable prefix."""
    shared_text = f"{global_shared.strip()}\n{conversation_shared.strip()}".strip()
    if not shared_text:
        return "prefix-empty"
    return hashlib.sha256(shared_text.encode("utf-8")).hexdigest()[:16]


def create_prefix_partition(
    *,
    global_shared: str = "",
    conversation_shared: str = "",
    unique_suffix: str = "",
) -> PrefixPartition:
    """Create a PrefixPartition calculating stable prefix_id and token estimates."""
    clean_global = global_shared.strip()
    clean_conv = conversation_shared.strip()
    clean_suffix = unique_suffix.strip()

    shared_combined = f"{clean_global}\n{clean_conv}".strip()
    prefix_id = compute_prefix_id(clean_global, clean_conv)

    shared_tokens = estimate_tokens(shared_combined)
    total_tokens = estimate_tokens(f"{shared_combined}\n{clean_suffix}".strip())

    return PrefixPartition(
        global_shared=clean_global,
        conversation_shared=clean_conv,
        unique_suffix=clean_suffix,
        prefix_id=prefix_id,
        estimated_total_tokens=total_tokens,
        estimated_shared_tokens=shared_tokens,
    )


def build_ask_partition(
    prompt: str,
    *,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    prior_messages: Sequence[Mapping[str, str]] | None = None,
) -> PrefixPartition:
    """Build partition for a direct ask query."""
    conv_parts = []
    if prior_messages:
        for msg in prior_messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            conv_parts.append(f"{role.upper()}: {content}")

    return create_prefix_partition(
        global_shared=system_prompt,
        conversation_shared="\n".join(conv_parts),
        unique_suffix=f"USER: {prompt}",
    )


def build_query_proposal_partition(
    prompt: str,
    schema: Mapping[str, Any],
    *,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    prior_turns: Sequence[Mapping[str, Any]] | None = None,
) -> PrefixPartition:
    """Build partition for proposing a governed taxi query."""
    schema_json = json.dumps(schema, separators=(",", ":"), sort_keys=True)
    global_shared = (
        f"{system_prompt}\n{DEFAULT_TAXI_RULES}\n"
        f"Available Dataset Schema: {schema_json}\n"
        "Choose exactly ONE governed analysis that answers the question. "
        "Do not make multiple tool calls. To compare all boroughs across NYC, "
        "call average_trip_metrics with region_name omitted."
    )

    conv_parts = []
    if prior_turns:
        for turn in prior_turns:
            conv_parts.append(f"PAST_TURN: {json.dumps(turn, sort_keys=True)}")

    unique_suffix = f"Question: {prompt}"
    return create_prefix_partition(
        global_shared=global_shared,
        conversation_shared="\n".join(conv_parts),
        unique_suffix=unique_suffix,
    )


def build_query_answer_partition(
    prompt: str,
    query_result: Mapping[str, Any],
    *,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    schema: Mapping[str, Any] | None = None,
    prior_turns: Sequence[Mapping[str, Any]] | None = None,
) -> PrefixPartition:
    """Build partition for generating an answer from DuckDB query results."""
    global_shared_parts = [system_prompt, DEFAULT_TAXI_RULES]
    if schema:
        global_shared_parts.append(f"Schema: {json.dumps(schema, sort_keys=True)}")
    global_shared = "\n".join(global_shared_parts)

    conv_parts = []
    if prior_turns:
        for turn in prior_turns:
            conv_parts.append(f"PAST_TURN: {json.dumps(turn, sort_keys=True)}")

    # Tool observation belongs to conversation context
    result_json = json.dumps(query_result, separators=(",", ":"), sort_keys=True)
    conv_parts.append(f"Observation (query_result): {result_json}")

    unique_suffix = (
        f"Answer the user's question using only this governed query result.\n"
        f"Question: {prompt}"
    )

    return create_prefix_partition(
        global_shared=global_shared,
        conversation_shared="\n".join(conv_parts),
        unique_suffix=unique_suffix,
    )


def build_dataset_profile_partition(
    prompt: str,
    dataset_profile: Mapping[str, Any],
    *,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> PrefixPartition:
    """Build partition for answering with a dataset profile."""
    profile_json = json.dumps(dataset_profile, separators=(",", ":"), sort_keys=True)
    conv_shared = f"Dataset Profile: {profile_json}"
    unique_suffix = (
        f"Answer the user's question using only this governed dataset profile.\n"
        f"Question: {prompt}"
    )
    return create_prefix_partition(
        global_shared=system_prompt,
        conversation_shared=conv_shared,
        unique_suffix=unique_suffix,
    )
