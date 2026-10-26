"""#178: versioned prompt templates render byte-identically to the pre-versioning text,
reject bad variables, and cannot change without a version bump."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from app.llm import taxi_proposal_prompt
from app.prefix import (
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_TAXI_RULES,
    build_ask_partition,
    build_dataset_profile_partition,
    build_query_answer_partition,
    build_query_proposal_partition,
)
from app.prompts import (
    ANSWER,
    ANSWER_SINGLE_TURN,
    DATASET_PROFILE_SINGLE_TURN,
    PLANNER,
    REGISTRY,
    PromptBundle,
    PromptKind,
    PromptRenderError,
    PromptTemplate,
)

GOLDEN = json.loads((Path(__file__).parent / "golden" / "prompts.json").read_text())
PINS = Path(__file__).parent / "golden" / "template_pins.json"
IN = GOLDEN["inputs"]
Q, SCHEMA = IN["question"], IN["schema"]


def _p(x) -> dict[str, str]:
    return {
        "global_shared": x.global_shared,
        "conversation_shared": x.conversation_shared,
        "unique_suffix": x.unique_suffix,
        "prefix_id": x.prefix_id,
    }


# --- golden: rendered output is byte-identical to the pre-extraction behaviour ---------


def test_constants_unchanged() -> None:
    assert DEFAULT_SYSTEM_PROMPT == GOLDEN["system_prompt"]
    assert DEFAULT_TAXI_RULES == GOLDEN["taxi_rules"]


def test_planner_single_turn_carries_the_decline_and_anchor_clauses() -> None:
    """#179: measured wording; no leaked 'no tool', answerable questions anchored to a tool."""
    msg = taxi_proposal_prompt(Q, SCHEMA)
    assert "only reads data and cannot change it" in msg
    assert "does not contain that information" in msg
    assert "dataset must use a tool" in msg
    assert "no tool" not in msg
    assert "call no tool" not in msg


@pytest.mark.parametrize(
    ("key", "build"),
    [
        ("planner", lambda: build_query_proposal_partition(Q, SCHEMA)),
        (
            "planner_prior",
            lambda: build_query_proposal_partition(
                Q, SCHEMA, prior_turns=IN["prior_turns"]
            ),
        ),
        (
            "planner_custom_system",
            lambda: build_query_proposal_partition(
                Q, SCHEMA, system_prompt="CUSTOM SYS"
            ),
        ),
        ("answer", lambda: build_query_answer_partition(Q, IN["query_result"])),
        (
            "answer_schema_prior",
            lambda: build_query_answer_partition(
                Q, IN["query_result"], schema=SCHEMA, prior_turns=IN["prior_turns"]
            ),
        ),
        ("dataset_profile", lambda: build_dataset_profile_partition(Q, IN["profile"])),
        (
            "ask",
            lambda: build_ask_partition(Q, prior_messages=IN["prior_messages"]),
        ),
        ("ask_no_prior", lambda: build_ask_partition(Q)),
    ],
)
def test_partitions_are_byte_identical_to_golden(key: str, build) -> None:
    assert _p(build()) == GOLDEN[key]


def test_single_turn_prompts_are_byte_identical_to_golden() -> None:
    assert taxi_proposal_prompt(Q, SCHEMA) == GOLDEN["planner_single_turn"]
    result_json = json.dumps(IN["query_result"], separators=(",", ":"))
    assert (
        ANSWER_SINGLE_TURN.render("message", question=Q, result_json=result_json)
        == GOLDEN["answer_single_turn"]
    )
    profile_json = json.dumps(IN["profile"], separators=(",", ":"))
    assert (
        DATASET_PROFILE_SINGLE_TURN.render(
            "message", question=Q, profile_json=profile_json
        )
        == GOLDEN["profile_single_turn"]
    )


# --- strict rendering ----------------------------------------------------------------


def test_missing_and_unexpected_variables_are_rejected() -> None:
    with pytest.raises(PromptRenderError, match="missing"):
        PLANNER.render("user")
    with pytest.raises(PromptRenderError, match="unexpected"):
        PLANNER.render("user", question="q", extra="x")
    with pytest.raises(PromptRenderError):
        PLANNER.render("user", question=1)  # type: ignore[arg-type]


def test_substituted_values_are_never_re_expanded() -> None:
    text = PLANNER.render("user", question="{{question}} {{system_prompt}}")
    assert text == "Question: {{question}} {{system_prompt}}"


def test_system_instructions_cannot_take_untrusted_variables() -> None:
    with pytest.raises(PromptRenderError, match="untrusted"):
        PromptTemplate("x {{q}}", PromptKind.SYSTEM_INSTRUCTIONS, frozenset({"q"}))
    with pytest.raises(PromptRenderError, match="not in template"):
        PromptTemplate("x", PromptKind.USER_CONTENT, frozenset({"q"}))


def test_bundle_version_must_be_semver() -> None:
    with pytest.raises(PromptRenderError, match="MAJOR"):
        PromptBundle(
            "x", "v1", {"a": PromptTemplate("t", PromptKind.USER_CONTENT)}, primary="a"
        )


def test_untrusted_regions_never_leak_into_instruction_regions() -> None:
    part = build_query_answer_partition("Q-MARK", {"rows": ["OBS-MARK"]})
    assert "OBS-MARK" not in part.global_shared and "Q-MARK" not in part.global_shared
    assert "OBS-MARK" in part.conversation_shared
    for bundle in REGISTRY.values():
        system = [
            t for t in bundle.parts.values() if t.kind is PromptKind.SYSTEM_INSTRUCTIONS
        ]
        assert all(not t.untrusted for t in system)


# --- immutability: a text change without a version bump fails ------------------------


def test_every_bundle_is_pinned_and_unchanged_without_a_version_bump() -> None:
    pins = json.loads(PINS.read_text())
    assert set(pins) == set(REGISTRY), "register new bundles in template_pins.json"
    for template_id, bundle in REGISTRY.items():
        pin = pins[template_id]
        if pin["version"] == bundle.version:
            assert pin["fingerprint"] == bundle.fingerprint(), (
                f"{template_id} text changed but version is still {bundle.version}: "
                "bump the version, update template_pins.json and docs/prompts.md"
            )
        else:
            pytest.fail(
                f"{template_id} is {bundle.version} but pin says {pin['version']}: "
                "update tests/golden/template_pins.json and the changelog"
            )


def test_editing_a_used_bundle_changes_dependents() -> None:
    assert ANSWER.fingerprint() != PLANNER.fingerprint()
    base = ANSWER.uses[0]
    edited = PromptBundle(
        base.template_id,
        base.version,
        {
            **base.parts,
            "rules": PromptTemplate("changed", PromptKind.SYSTEM_INSTRUCTIONS),
        },
        primary=base.primary,
    )
    changed = PromptBundle(
        ANSWER.template_id, ANSWER.version, ANSWER.parts, ANSWER.primary, (edited,)
    )
    assert changed.fingerprint() != ANSWER.fingerprint()


# --- attribution ---------------------------------------------------------------------


def test_partition_attribution_is_reproducible_and_role_specific() -> None:
    planner = build_query_proposal_partition(Q, SCHEMA)
    again = build_query_proposal_partition("another question", SCHEMA)
    answer = build_query_answer_partition(Q, IN["query_result"])
    assert planner.attribution is not None and answer.attribution is not None
    assert planner.attribution.template_id == "planner"
    assert answer.attribution.template_id == "answer"
    assert planner.attribution.version == "1.0.0"
    # Hash covers the exact instruction text sent, not user content.
    assert (
        planner.attribution.sha256
        == hashlib.sha256(planner.global_shared.encode()).hexdigest()
    )
    assert planner.attribution == again.attribution
    assert planner.attribution.sha256 != answer.attribution.sha256
    other_schema = build_query_proposal_partition(Q, {"columns": ["x"]})
    assert other_schema.attribution.sha256 != planner.attribution.sha256
