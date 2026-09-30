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
    current_message_id: str | None = None,
) -> RenderedPrompt:
    """Build the real, conversational messages list for a model call:

    [system(global_shared), *stored_messages_for_this_conversation_only,
     user(new_question)]

    Only messages stored under `conversation_id` are included, so two
    conversations sharing the same global system prompt never leak each
    other's history (D16). Stored `role="tool"` messages (D18) are included
    exactly like user/assistant turns, giving the model real prior-turn
    context, including prior tool observations.

    `prepare_run` durably persists the current turn's user message *before*
    this renderer runs, so the stored history already contains it. Without
    `current_message_id`, appending `new_question` again would duplicate the
    current question in the rendered payload. When `current_message_id` is
    given, the stored message with that id is excluded from history so it is
    rendered exactly once (as the trailing `user(new_question)`). Callers
    that don't have a message id (e.g. tests building history directly) can
    omit it; in that case a stored trailing user message whose content
    already equals `new_question` is treated as the current turn and
    excluded the same way.
    """
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    if conversation_id is not None:
        stored_messages = repo.list_messages(conversation_id)
        if current_message_id is not None:
            history = [
                stored
                for stored in stored_messages
                if stored.message_id != current_message_id
            ]
        elif (
            stored_messages
            and stored_messages[-1].role == "user"
            and stored_messages[-1].content == new_question
        ):
            history = stored_messages[:-1]
        else:
            history = stored_messages
        for stored in history:
            messages.append({"role": stored.role, "content": stored.content})
    messages.append({"role": "user", "content": new_question})
    return RenderedPrompt(
        messages=messages, prefix_contract_version=PREFIX_CONTRACT_VERSION
    )
