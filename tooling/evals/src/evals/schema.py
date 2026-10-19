"""Pydantic models: golden cases, capture.v1 trace records, and the run manifest.

The ``TraceRecord`` mirrors the shared ``capture.v1`` contract owned by the capture track
(#169). This module only *consumes* it; unknown extra fields are ignored so the producer can
add fields without breaking evals.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

CAPTURE_SCHEMA_VERSION = "capture.v1"

Category = Literal["deterministic", "react", "multi_turn", "unsupported", "failure"]
Behavior = Literal["answer", "unsupported", "failure"]


class Fact(BaseModel):
    """One checkable value taken from the result of the reference SQL.

    ``value`` is filled by ``evals refresh-reference`` from hand-authored SQL, never by a model.
    """

    label: str
    kind: Literal["number", "text"]
    row: int = 0
    col: str
    rel_tol: float = 0.001
    value: float | str | None = None


class Reference(BaseModel):
    sql: str | None = None
    facts: list[Fact] = Field(default_factory=list)


class ExpectedTool(BaseModel):
    name: str
    arguments: dict[str, object] = Field(default_factory=dict)
    match: Literal["subset", "exact"] = "subset"


class FailureSpec(BaseModel):
    kind: Literal[
        "tool_validation_error", "tool_timeout", "empty_result", "model_provider_error"
    ]
    tool_status: str | None = None


class Turn(BaseModel):
    question: str
    behavior: Behavior = "answer"
    expected_tools: list[ExpectedTool] = Field(default_factory=list)
    max_steps: int = 3
    reference: Reference = Field(default_factory=Reference)
    must_mention_any: list[str] = Field(default_factory=list)
    failure: FailureSpec | None = None

    def facts_without_sql(self) -> bool:
        return bool(self.reference.facts) and not self.reference.sql


class Review(BaseModel):
    status: Literal["pending_human_review", "reviewed"]
    reviewer: str | None = None


class GoldenCase(BaseModel):
    id: str
    version: str
    category: Category
    source: Literal["golden"] = "golden"
    tags: list[str] = Field(default_factory=list)
    notes: str = ""
    review: Review
    turns: list[Turn] = Field(min_length=1)


class TraceRecord(BaseModel):
    """One capture.v1 record (a model call or a tool call), joined to others by run_id."""

    model_config = ConfigDict(extra="ignore")

    schema_version: str = CAPTURE_SCHEMA_VERSION
    record_type: Literal["model_call", "tool_call"]
    run_id: str
    turn_id: str
    step: int
    call_id: str
    tenant_id: str | None = None
    trace_id: str | None = None
    # real capture only (#173): session grouping and run ordering
    conversation_id: str | None = None
    run_started_at: str | None = None
    run_status: str | None = None
    # model_call
    role: Literal["classifier", "planner", "answer"] | None = None
    model_alias: str | None = None
    provider_model_id: str | None = None
    prompt_template_version: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    ttft_ms: float | None = None
    latency_ms: float | None = None
    finish_reason: str | None = None
    error: str | None = None
    fallback_used: bool | None = None
    cost_usd: float | None = None
    cost_kind: Literal["estimated", "billed", "unknown"] | None = None
    pricing_version: str | None = None
    # tool_call
    tool_name: str | None = None
    tool_call_id: str | None = None
    arguments: dict[str, object] | None = None
    argument_keys: list[str] | None = None
    query_id: str | None = None
    status: str | None = None
    row_count: int | None = None
    # content (opt-in; otherwise null)
    question: str | None = None
    answer: str | None = None
    question_hash: str | None = None
    answer_hash: str | None = None
    # eval linkage (synthetic fixtures only): which golden case/turn this run answers.
    # Real capture never carries these; link runs explicitly with evals.matching.
    case_id: str | None = None
    turn_index: int = 0


class Thresholds(BaseModel):
    """Regression gates for candidate-vs-baseline comparison (all deltas are candidate - baseline)."""

    min_pass_rate_deterministic: float = 0.9
    max_pass_rate_drop: float = 0.05
    max_tool_correctness_drop: float = 0.05
    max_judge_score_drop: float = 0.05
    max_failure_rate_increase: float = 0.05
    max_mean_steps_increase: float = 0.5
    max_cost_increase_pct: float = 25.0
    max_p95_latency_increase_pct: float = 25.0


class RunManifest(BaseModel):
    """Everything needed to reproduce and compare an eval run."""

    run_label: str
    dataset_version: str
    dataset_sha256: str
    duckdb_dataset: str
    model_alias: str
    prompt_version: str
    judge_model: str | None = None
    judge_mode: Literal["recorded", "live", "off"] = "recorded"
    capture_schema: str = CAPTURE_SCHEMA_VERSION
    thresholds: Thresholds = Field(default_factory=Thresholds)
