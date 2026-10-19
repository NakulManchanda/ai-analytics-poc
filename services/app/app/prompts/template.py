"""Strict, single-pass prompt templates and their immutable, versioned bundles.

A ``PromptTemplate`` is one region of a prompt (system instructions, user content, a
tool observation, ...). A ``PromptBundle`` is the unit that is versioned and attributed:
the set of regions one model call renders, under one ``template_id`` and one semantic
version. Rendering never executes or re-expands substituted values, so untrusted text
containing ``{{name}}`` stays inert.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

_PLACEHOLDER = re.compile(r"\{\{([a-z_][a-z0-9_]*)\}\}")
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


class PromptRenderError(ValueError):
    """Missing or unexpected variables, or an invalid template definition."""


class PromptKind(StrEnum):
    SYSTEM_INSTRUCTIONS = "system_instructions"  # trusted, never holds untrusted vars
    TRUSTED_DATA = "trusted_data"  # schema / catalogue facts
    USER_CONTENT = "user_content"  # untrusted user text
    TOOL_OBSERVATION = (
        "tool_observation"  # untrusted retrieved data, never instructions
    )
    HISTORY = "history"  # prior turns, untrusted
    SINGLE_TURN = "single_turn"  # legacy one-message prompt mixing regions


@dataclass(frozen=True)
class PromptTemplate:
    text: str
    kind: PromptKind
    # Variables that carry untrusted text (user input, tool output, history).
    untrusted: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        unknown = self.untrusted - self.variables
        if unknown:
            raise PromptRenderError(
                f"untrusted names not in template: {sorted(unknown)}"
            )
        if self.kind is PromptKind.SYSTEM_INSTRUCTIONS and self.untrusted:
            raise PromptRenderError(
                "system instructions cannot take untrusted variables"
            )

    @property
    def variables(self) -> frozenset[str]:
        return frozenset(_PLACEHOLDER.findall(self.text))

    def render(self, **values: str) -> str:
        missing = self.variables - values.keys()
        unexpected = values.keys() - self.variables
        if missing or unexpected:
            raise PromptRenderError(
                f"missing variables {sorted(missing)}, unexpected variables {sorted(unexpected)}"
            )
        for name, value in values.items():
            if not isinstance(value, str):
                raise PromptRenderError(f"variable {name!r} must be str")
        # Single pass: substituted values are never re-scanned for placeholders.
        return _PLACEHOLDER.sub(lambda m: values[m.group(1)], self.text)


@dataclass(frozen=True)
class PromptAttribution:
    """What one model call actually rendered: bundle id, immutable version, and the
    SHA-256 of the exact instruction text sent (the bundle's ``primary`` region)."""

    template_id: str
    version: str
    sha256: str

    def to_dict(self) -> dict[str, str]:
        return {
            "prompt_template_id": self.template_id,
            "prompt_template_version": self.version,
            "prompt_sha256": self.sha256,
        }


@dataclass(frozen=True)
class PromptBundle:
    template_id: str
    version: str
    parts: Mapping[str, PromptTemplate]
    primary: str  # part whose rendered text is hashed for attribution
    uses: tuple[PromptBundle, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        if not _SEMVER.match(self.version):
            raise PromptRenderError(
                f"{self.template_id}: version must be MAJOR.MINOR.PATCH"
            )
        if self.primary not in self.parts:
            raise PromptRenderError(f"{self.template_id}: unknown primary part")

    def render(self, part: str, **values: str) -> str:
        return self.parts[part].render(**values)

    def attribute(self, rendered_primary: str) -> PromptAttribution:
        return PromptAttribution(
            self.template_id,
            self.version,
            hashlib.sha256(rendered_primary.encode("utf-8")).hexdigest(),
        )

    def fingerprint(self) -> str:
        """Hash of every source text this revision depends on. A change to any part (or
        to a bundle it uses) changes this, and the pin test then demands a version bump.
        """
        canonical = json.dumps(
            {
                "id": self.template_id,
                "parts": {
                    name: [t.kind.value, t.text, sorted(t.untrusted)]
                    for name, t in sorted(self.parts.items())
                },
                "uses": [u.fingerprint() for u in self.uses],
            },
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
