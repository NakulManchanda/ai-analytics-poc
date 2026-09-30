"""Tests for the prefix contract v2 renderer (services/app/app/prefix_render.py).

Direct regression coverage for the "second highest zone" bug: turn 2 of a
stored conversation must actually see turn 1's tool result in the rendered
messages, not just the current turn's result.
"""

from __future__ import annotations

from app.prefix_render import PREFIX_CONTRACT_VERSION, render_conversation_prompt
from app.state import InMemoryStateRepository, Message


def _add(
    repo: InMemoryStateRepository, conv_id: str, seq: int, role: str, content: str
) -> None:
    repo.add_message(
        Message(
            message_id=f"msg-{conv_id}-{seq}",
            conversation_id=conv_id,
            sequence=seq,
            role=role,
            content=content,
        )
    )


def test_turn_two_sees_turn_one_tool_result() -> None:
    """Direct regression test for the Staten-Island screenshot bug: turn 2's
    rendered prompt must contain turn 1's tool observation verbatim."""
    from app.state import Conversation

    repo = InMemoryStateRepository()
    conv_id = "conv-turns"
    repo.create_conversation(Conversation(conversation_id=conv_id))

    _add(repo, conv_id, 1, "user", "Which pickup zone has the most trips?")
    _add(
        repo,
        conv_id,
        2,
        "tool",
        '{"columns":["pickup_zone","trip_count"],"rows":[["Staten Island",42]]}',
    )
    _add(repo, conv_id, 3, "assistant", "Staten Island leads with 42 trips.")

    rendered = render_conversation_prompt(
        conv_id, "Compare that with the second highest zone.", repo
    )

    all_content = " ".join(m["content"] for m in rendered.messages)
    assert "Staten Island" in all_content
    assert '"pickup_zone"' in all_content
    assert rendered.messages[-1] == {
        "role": "user",
        "content": "Compare that with the second highest zone.",
    }
    assert (
        rendered.prefix_contract_version
        == PREFIX_CONTRACT_VERSION
        == "v2-conversational"
    )


def test_two_conversations_do_not_leak_history() -> None:
    from app.state import Conversation

    repo = InMemoryStateRepository()
    repo.create_conversation(Conversation(conversation_id="conv-a"))
    repo.create_conversation(Conversation(conversation_id="conv-b"))

    _add(repo, "conv-a", 1, "user", "Secret question A")
    _add(repo, "conv-a", 2, "assistant", "Secret answer A")
    _add(repo, "conv-b", 1, "user", "Unrelated question B")

    rendered_b = render_conversation_prompt("conv-b", "Follow-up for B", repo)

    contents = [m["content"] for m in rendered_b.messages]
    assert "Secret question A" not in contents
    assert "Secret answer A" not in contents
    assert "Unrelated question B" in contents


def test_rendering_is_deterministic_and_versioned() -> None:
    from app.state import Conversation

    repo = InMemoryStateRepository()
    repo.create_conversation(Conversation(conversation_id="conv-det"))
    _add(repo, "conv-det", 1, "user", "First question")
    _add(repo, "conv-det", 2, "assistant", "First answer")

    first = render_conversation_prompt("conv-det", "Second question", repo)
    second = render_conversation_prompt("conv-det", "Second question", repo)

    assert first.messages == second.messages
    assert first.prefix_contract_version == second.prefix_contract_version


def test_render_with_no_conversation_id_is_single_turn() -> None:
    repo = InMemoryStateRepository()
    rendered = render_conversation_prompt(None, "A bare question", repo)
    assert rendered.messages[-1] == {"role": "user", "content": "A bare question"}
    assert len(rendered.messages) == 2  # system + user only
