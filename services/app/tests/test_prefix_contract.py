"""Tests for the Prefix Token Contract module."""

from app.prefix import (
    DEFAULT_SYSTEM_PROMPT,
    build_ask_partition,
    build_dataset_profile_partition,
    build_query_answer_partition,
    build_query_proposal_partition,
    compute_prefix_id,
    estimate_tokens,
)


def test_estimate_tokens_empty_and_normal() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("   ") == 0
    assert estimate_tokens("hello") >= 1
    sample = "This is a quick test of token estimation for seven words."
    tokens = estimate_tokens(sample)
    assert 9 <= tokens <= 15


def test_compute_prefix_id_stability() -> None:
    id1 = compute_prefix_id("global system text", "conv message 1")
    id2 = compute_prefix_id("global system text", "conv message 1")
    assert id1 == id2
    assert len(id1) == 16

    # Different conversation text produces different hash
    id3 = compute_prefix_id("global system text", "conv message 2")
    assert id1 != id3

    # Empty fallback
    assert compute_prefix_id("", "") == "prefix-empty"


def test_prefix_caching_invariant_unique_suffix_does_not_alter_prefix_id() -> None:
    """Verifies that differing unique suffixes share the exact same prefix_id."""
    schema = {"columns": ["pickup_zone", "trip_count"]}
    part1 = build_query_proposal_partition("What is the top pickup zone?", schema)
    part2 = build_query_proposal_partition("Which zone had the most rides?", schema)

    # Identical global and conversation context -> identical prefix_id
    assert part1.prefix_id == part2.prefix_id
    assert part1.global_shared == part2.global_shared
    assert part1.conversation_shared == part2.conversation_shared
    # Suffixes differ
    assert part1.unique_suffix != part2.unique_suffix
    assert part1.full_prompt != part2.full_prompt


def test_conversation_shared_growth_updates_prefix_id() -> None:
    """Adding tool observations / prior turns creates an updated prefix_id for subsequent steps."""
    schema = {"columns": ["pickup_zone", "trip_count"]}
    query_result_1 = {"row_count": 5, "rows": [["JFK", 1200]]}
    query_result_2 = {"row_count": 10, "rows": [["LaGuardia", 850]]}

    part1 = build_query_answer_partition(
        "Summarize top zones",
        query_result_1,
        schema=schema,
    )
    part2 = build_query_answer_partition(
        "Summarize top zones",
        query_result_2,
        schema=schema,
    )

    # Different query observation in conversation context -> different prefix_id
    assert part1.prefix_id != part2.prefix_id
    assert "JFK" in part1.conversation_shared
    assert "LaGuardia" in part2.conversation_shared


def test_build_ask_partition_structure() -> None:
    prior = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello"},
    ]
    part = build_ask_partition("Hello taxi agent", prior_messages=prior)
    assert DEFAULT_SYSTEM_PROMPT in part.global_shared
    assert "USER: Hi" in part.conversation_shared
    assert "ASSISTANT: Hello" in part.conversation_shared
    assert part.unique_suffix == "USER: Hello taxi agent"
    assert part.estimated_total_tokens >= part.estimated_shared_tokens


def test_build_dataset_profile_partition_structure() -> None:
    profile = {
        "row_count": 2500000,
        "columns": ["tpep_pickup_datetime", "trip_distance"],
    }
    part = build_dataset_profile_partition(
        "How many trips are in the dataset?", profile
    )
    assert DEFAULT_SYSTEM_PROMPT in part.global_shared
    assert "2500000" in part.conversation_shared
    assert "How many trips are in the dataset?" in part.unique_suffix


def test_exact_token_counts_unavailable_when_not_serve_mode() -> None:
    """D17: never estimate from word count for the exact-count field; when not
    in serve mode, counts stay None with a reason."""
    from app.prefix import build_ask_partition, count_prefix_tokens_exact

    partition = build_ask_partition("What are the busiest pickup zones?")
    result = count_prefix_tokens_exact(
        partition, model_id="fake-model", gateway_url=None, is_serve_mode=False
    )
    assert result.global_shared_tokens is None
    assert result.conversation_shared_tokens is None
    assert result.unique_suffix_tokens is None
    assert result.token_count_unavailable_reason == "not_serve_mode"


def test_exact_token_counts_unavailable_when_tokenize_call_fails() -> None:
    """A failed /tokenize call (serve mode) must also never fall back to the
    word-count estimator -- counts stay None with a reason."""
    import httpx
    from app.prefix import build_ask_partition, count_prefix_tokens_exact

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    partition = build_ask_partition("What are the busiest pickup zones?")
    result = count_prefix_tokens_exact(
        partition,
        model_id="fake-model",
        gateway_url="http://localhost:18080/serve",
        is_serve_mode=True,
        http_client=client,
    )
    assert result.global_shared_tokens is None
    assert result.token_count_unavailable_reason is not None
    assert result.token_count_unavailable_reason.startswith("tokenize_call_failed")


def test_exact_token_counts_succeed_via_gateway_tokenize() -> None:
    import httpx
    from app.prefix import build_ask_partition, count_prefix_tokens_exact

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"count": 7, "tokens": list(range(7))})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    partition = build_ask_partition("What are the busiest pickup zones?")
    result = count_prefix_tokens_exact(
        partition,
        model_id="fake-model",
        gateway_url="http://localhost:18080/serve",
        is_serve_mode=True,
        http_client=client,
    )
    assert result.token_count_unavailable_reason is None
    assert result.global_shared_tokens == 7
    assert result.conversation_shared_tokens == 0
    assert result.unique_suffix_tokens == 7
