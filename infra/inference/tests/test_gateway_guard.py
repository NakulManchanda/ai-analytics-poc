"""Guard tests: caller headers cannot lower the estimate; prompt + max_tokens must fit the window."""

import pytest

from infra.inference.gateway.guard import (
    CONTEXT_TOO_LONG,
    PROMPT_TOO_LONG,
    estimate_prompt_tokens,
    inspect,
    requested_completion_tokens,
)

SHORT = [{"role": "user", "content": "hello"}]


def _long(tokens: int) -> list[dict]:
    return [{"role": "user", "content": "x" * (tokens * 4)}]


def test_header_cannot_lower_the_estimate() -> None:
    own = estimate_prompt_tokens({"messages": _long(1000)})
    assert estimate_prompt_tokens({"messages": _long(1000)}, "0") == own
    assert estimate_prompt_tokens({"messages": _long(1000)}, "5") == own


def test_header_can_raise_the_estimate() -> None:
    assert estimate_prompt_tokens({"messages": SHORT}, "5000") == 5000


@pytest.mark.parametrize("header", ["-1", "abc", ""])
def test_invalid_header_is_ignored(header) -> None:
    assert estimate_prompt_tokens({"messages": SHORT}, header) == estimate_prompt_tokens(
        {"messages": SHORT}
    )


def test_zero_header_does_not_bypass_prompt_limit() -> None:
    verdict = inspect({"messages": _long(9000)}, estimated_tokens_header="0", max_tokens=8192)
    assert not verdict.ok and verdict.code == PROMPT_TOO_LONG and verdict.http_status == 413


@pytest.mark.parametrize(
    "extra,expected",
    [({}, 0), ({"max_tokens": 128}, 128), ({"max_completion_tokens": 64, "max_tokens": 9}, 64)],
)
def test_requested_completion_tokens(extra, expected) -> None:
    assert requested_completion_tokens({"messages": SHORT, **extra}) == expected


@pytest.mark.parametrize("bad", [-1, True, "128", None, 1.5])
def test_invalid_completion_tokens_count_as_zero(bad) -> None:
    assert requested_completion_tokens({"max_tokens": bad}) == 0


def test_prompt_plus_max_tokens_over_window_is_rejected() -> None:
    payload = {"messages": _long(8100), "max_tokens": 128}
    verdict = inspect(payload, max_tokens=32768, model_len=8192)
    assert not verdict.ok and verdict.code == CONTEXT_TOO_LONG and verdict.http_status == 413


def test_prompt_plus_max_tokens_at_window_is_allowed() -> None:
    payload = {"messages": _long(8000), "max_tokens": 128}
    assert inspect(payload, max_tokens=32768, model_len=8192).ok


def test_window_check_disabled_when_unset(monkeypatch) -> None:
    monkeypatch.delenv("MAX_MODEL_LEN", raising=False)
    assert inspect({"messages": _long(9000), "max_tokens": 128}, max_tokens=32768).ok


def test_window_comes_from_env(monkeypatch) -> None:
    monkeypatch.setenv("MAX_MODEL_LEN", "1000")
    verdict = inspect({"messages": _long(1000), "max_tokens": 1}, max_tokens=32768)
    assert not verdict.ok and verdict.code == CONTEXT_TOO_LONG
