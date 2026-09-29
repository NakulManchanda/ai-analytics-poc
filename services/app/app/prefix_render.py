"""Prefix contract v2: renders an OpenAI-style messages list from real stored
conversation history (D16-D19), replacing the single-turn payload construction
that previously only ever sent the current tool result.

``render_conversation_prompt`` is deterministic: the same stored conversation
state plus the same new question always produces a byte-identical messages
list. No timestamps, ids, or other non-deterministic data are included in the
rendered payload itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.prefix import DEFAULT_SYSTEM_PROMPT

if TYPE_CHECKING:
    from app.state import StateRepository

PREFIX_CONTRACT_VERSION = "v2-conversational"


@dataclass(frozen=True)
class RenderedPrompt:
    """The rendered OpenAI-style messages list plus the contract version that
    produced it, so callers/telemetry can record which rendering contract was
    used without guessing."""

    messages: list[dict[str, str]] = field(default_factory=list)
    prefix_contract_version: str = PREFIX_CONTRACT_VERSION


def render_conversation_prompt(
    conversation_id: str | None,
    new_question: str,
    repo: "StateRepository",
    *,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
) -> RenderedPrompt:
    """Build the real, conversational messages list for a model call:

    [system(global_shared), *stored_messages_for_this_conversation_only,
     user(new_question)]

    Only messages stored under `conversation_id` are included, so two
    conversations sharing the same global system prompt never leak each
    other's history (D16). Stored `role="tool"` messages (D18) are included
    exactly like user/assistant turns, giving the model real prior-turn
    context, including prior tool observations.
    """
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    if conversation_id is not None:
        for stored in repo.list_messages(conversation_id):
            messages.append({"role": stored.role, "content": stored.content})
    messages.append({"role": "user", "content": new_question})
    return RenderedPrompt(messages=messages, prefix_contract_version=PREFIX_CONTRACT_VERSION)
