"""Versioned prompt bundles for the taxi analytics assistant.

Every text here is byte-for-byte the text the application sent before versioning
(#178 first migration). Changing any text requires a new ``version`` and a matching
update to ``tests/golden/template_pins.json`` plus a ``docs/prompts.md`` changelog
entry; the pin test fails otherwise.
"""

from __future__ import annotations

from app.prompts.template import PromptBundle, PromptKind, PromptTemplate

SYSTEM = PromptKind.SYSTEM_INSTRUCTIONS

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

# Static shared base text. Not attributed on its own: bundles that embed it list it in
# ``uses`` so editing it forces their version bump.
TAXI_BASE = PromptBundle(
    "taxi-base",
    "1.0.0",
    {
        "system": PromptTemplate(DEFAULT_SYSTEM_PROMPT, SYSTEM),
        "rules": PromptTemplate(DEFAULT_TAXI_RULES, SYSTEM),
    },
    primary="system",
    description="Default system prompt and dataset rules shared by every role.",
)

_PAST_TURN = PromptTemplate(
    "PAST_TURN: {{turn_json}}", PromptKind.HISTORY, frozenset({"turn_json"})
)
_QUERY_OBSERVATION = PromptTemplate(
    "Observation (query_result): {{result_json}}",
    PromptKind.TOOL_OBSERVATION,
    frozenset({"result_json"}),
)
_ANSWER_USER = PromptTemplate(
    "Answer the user's question using only this governed query result.\n"
    "Question: {{question}}",
    PromptKind.USER_CONTENT,
    frozenset({"question"}),
)

PLANNER = PromptBundle(
    "planner",
    "1.0.0",
    {
        "global": PromptTemplate(
            "{{system_prompt}}\n{{taxi_rules}}\n"
            "Available Dataset Schema: {{schema_json}}\n"
            "Choose exactly ONE governed analysis that answers the question. "
            "Do not make multiple tool calls. To compare all boroughs across NYC, "
            "call average_trip_metrics with region_name omitted.",
            SYSTEM,
        ),
        "past_turn": _PAST_TURN,
        "user": PromptTemplate(
            "Question: {{question}}", PromptKind.USER_CONTENT, frozenset({"question"})
        ),
    },
    primary="global",
    uses=(TAXI_BASE,),
    description="Planner tool proposal on the partitioned (serve/LiteLLM-history) path.",
)

ANSWER = PromptBundle(
    "answer",
    "1.0.0",
    {
        "global": PromptTemplate("{{system_prompt}}\n{{taxi_rules}}", SYSTEM),
        "past_turn": _PAST_TURN,
        "observation": _QUERY_OBSERVATION,
        "user": _ANSWER_USER,
    },
    primary="global",
    uses=(TAXI_BASE,),
    description="Final answer synthesis from a governed query result (partitioned path).",
)

ANSWER_WITH_SCHEMA = PromptBundle(
    "answer-with-schema",
    "1.0.0",
    {
        "global": PromptTemplate(
            "{{system_prompt}}\n{{taxi_rules}}\nSchema: {{schema_json}}", SYSTEM
        ),
        "past_turn": _PAST_TURN,
        "observation": _QUERY_OBSERVATION,
        "user": _ANSWER_USER,
    },
    primary="global",
    uses=(TAXI_BASE,),
    description="Final answer synthesis when the dataset schema is included.",
)

DATASET_PROFILE = PromptBundle(
    "dataset-profile",
    "1.0.0",
    {
        "global": PromptTemplate("{{system_prompt}}", SYSTEM),
        "observation": PromptTemplate(
            "Dataset Profile: {{profile_json}}",
            PromptKind.TOOL_OBSERVATION,
            frozenset({"profile_json"}),
        ),
        "user": PromptTemplate(
            "Answer the user's question using only this governed dataset profile.\n"
            "Question: {{question}}",
            PromptKind.USER_CONTENT,
            frozenset({"question"}),
        ),
    },
    primary="global",
    uses=(TAXI_BASE,),
    description="Answer from the dataset profile (partitioned path).",
)

ASK = PromptBundle(
    "ask",
    "1.0.0",
    {
        "global": PromptTemplate("{{system_prompt}}", SYSTEM),
        "history": PromptTemplate(
            "{{role}}: {{content}}", PromptKind.HISTORY, frozenset({"content"})
        ),
        "user": PromptTemplate(
            "USER: {{prompt}}", PromptKind.USER_CONTENT, frozenset({"prompt"})
        ),
    },
    primary="global",
    uses=(TAXI_BASE,),
    description="Direct ask with no tools.",
)

AGENT_GLOBAL = PromptBundle(
    "agent-global",
    "1.0.0",
    {"global": PromptTemplate("{{system_prompt}}\n{{taxi_rules}}", SYSTEM)},
    primary="global",
    uses=(TAXI_BASE,),
    description=(
        "Shared instruction region for the CrewAI researcher/writer calls. Task, goal "
        "and backstory text is composed by CrewAI and is not yet versioned."
    ),
)

PLANNER_SINGLE_TURN = PromptBundle(
    "planner-single-turn",
    "1.0.0",
    {
        "message": PromptTemplate(
            "Choose exactly ONE governed analysis that answers the question. "
            "Do not make multiple tool calls. To compare all boroughs "
            "across NYC, call average_trip_metrics with region_name omitted. "
            "Dataset schema: {{schema_json}}\nQuestion: {{question}}",
            PromptKind.SINGLE_TURN,
            frozenset({"question"}),
        )
    },
    primary="message",
    description="Planner for direct Bedrock and the LiteLLM proxy (one user message).",
)

ANSWER_SINGLE_TURN = PromptBundle(
    "answer-single-turn",
    "1.0.0",
    {
        "message": PromptTemplate(
            "Answer the user's question using only this governed query "
            "result. Question: {{question}}\nQuery result: {{result_json}}",
            PromptKind.SINGLE_TURN,
            frozenset({"question", "result_json"}),
        )
    },
    primary="message",
    description="Direct Bedrock answer synthesis (one user message, streaming or not).",
)

DATASET_PROFILE_SINGLE_TURN = PromptBundle(
    "dataset-profile-single-turn",
    "1.0.0",
    {
        "message": PromptTemplate(
            "Answer the user's question using only this governed dataset "
            "profile. Question: {{question}}\nDataset profile: {{profile_json}}",
            PromptKind.SINGLE_TURN,
            frozenset({"question", "profile_json"}),
        )
    },
    primary="message",
    description="Direct Bedrock dataset-profile answer (one user message).",
)

REGISTRY: dict[str, PromptBundle] = {
    b.template_id: b
    for b in (
        TAXI_BASE,
        PLANNER,
        ANSWER,
        ANSWER_WITH_SCHEMA,
        DATASET_PROFILE,
        ASK,
        AGENT_GLOBAL,
        PLANNER_SINGLE_TURN,
        ANSWER_SINGLE_TURN,
        DATASET_PROFILE_SINGLE_TURN,
    )
}
