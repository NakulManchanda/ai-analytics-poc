"""Versioned prompt templates (#178). See docs/prompts.md."""

from app.prompts.bundles import (
    AGENT_GLOBAL,
    ANSWER,
    ANSWER_SINGLE_TURN,
    ANSWER_WITH_SCHEMA,
    ASK,
    DATASET_PROFILE,
    DATASET_PROFILE_SINGLE_TURN,
    DEFAULT_SYSTEM_PROMPT,
    DEFAULT_TAXI_RULES,
    PLANNER,
    PLANNER_SINGLE_TURN,
    REGISTRY,
    TAXI_BASE,
)
from app.prompts.template import (
    PromptAttribution,
    PromptBundle,
    PromptKind,
    PromptRenderError,
    PromptTemplate,
)

__all__ = [
    "AGENT_GLOBAL",
    "ANSWER",
    "ANSWER_SINGLE_TURN",
    "ANSWER_WITH_SCHEMA",
    "ASK",
    "DATASET_PROFILE",
    "DATASET_PROFILE_SINGLE_TURN",
    "DEFAULT_SYSTEM_PROMPT",
    "DEFAULT_TAXI_RULES",
    "PLANNER",
    "PLANNER_SINGLE_TURN",
    "REGISTRY",
    "TAXI_BASE",
    "PromptAttribution",
    "PromptBundle",
    "PromptKind",
    "PromptRenderError",
    "PromptTemplate",
]
