"""Builds capture.v1 records from durable run state and writes them to a sink."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from opentelemetry import trace

from app.capture.schema import (
    RECORD_MODEL_CALL,
    RECORD_TOOL_CALL,
    SCHEMA_VERSION,
    sha256_hex,
)
from app.capture.sink import CaptureSink, JsonlCaptureSink

logger = logging.getLogger(__name__)
TRUNCATED = "…[truncated]"
_ROLE_BY_STEP = {"llm_proposal": "planner", "llm_final_answer": "answer"}


@dataclass(frozen=True)
class CaptureSettings:
    directory: Path | None = None
    include_content: bool = False
    max_field_bytes: int = 4096

    @classmethod
    def from_environment(cls) -> CaptureSettings:
        raw_dir = os.getenv("CAPTURE_DIR")
        return cls(
            directory=Path(raw_dir) if raw_dir else None,
            include_content=os.getenv("CAPTURE_CONTENT", "").lower()
            in ("1", "true", "yes"),
            max_field_bytes=int(os.getenv("CAPTURE_MAX_FIELD_BYTES", "4096")),
        )


def _usage(call: dict[str, Any], key: str) -> int | None:
    """Token counts are null exactly when the provider reported no usage; 0 is a real count."""
    if call.get("usage_known") is False:
        return None
    return call.get(key)


def _cost(call: dict[str, Any]) -> tuple[float | None, str, str | None]:
    """(cost_usd, cost_kind, pricing_version). Policy-priced calls report their own
    cost; calls from clients without the policy layer fall back to the loop estimate."""
    if call.get("cost_kind") is not None:
        return (
            call.get("result_cost_usd"),
            call["cost_kind"],
            call.get("pricing_version"),
        )
    legacy = call.get("cost_usd")
    if legacy:
        return legacy, "estimated", call.get("pricing_version")
    return None, "unknown", call.get("pricing_version")


def _trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


class CaptureRecorder:
    def __init__(self, settings: CaptureSettings, tenant_id: str = "default") -> None:
        if settings.directory is None:
            raise ValueError("CaptureSettings.directory is required")
        self.settings = settings
        self.tenant_id = tenant_id
        self.sink: CaptureSink = JsonlCaptureSink(settings.directory, tenant_id)
        self._failures = 0

    @classmethod
    def from_environment(cls, tenant_id: str = "default") -> CaptureRecorder | None:
        settings = CaptureSettings.from_environment()
        return cls(settings, tenant_id) if settings.directory else None

    @property
    def dropped(self) -> int:
        return self._failures + self.sink.dropped

    def cap(self, text: str | None) -> str | None:
        if text is None:
            return None
        limit = self.settings.max_field_bytes
        raw = text.encode("utf-8")
        if len(raw) <= limit:
            return text
        return raw[:limit].decode("utf-8", errors="ignore") + TRUNCATED

    def capture_run(
        self, repo: Any, run_id: str, question: str | None, answer: str | None
    ) -> None:
        """Best-effort: never raises."""
        try:
            for record in self._build(repo, run_id, question, answer):
                try:
                    self.sink.write(record)
                except Exception:
                    self._failures += 1
        except Exception:
            self._failures += 1
            logger.warning("capture build failed", exc_info=True)

    def _arguments_fields(self, summary: str | None) -> dict[str, Any]:
        """Metadata-only: argument keys + hash. Raw arguments only when opted in."""
        keys: list[str] | None = None
        if summary:
            try:
                parsed = json.loads(summary)
                if isinstance(parsed, dict):
                    keys = sorted(parsed)
            except ValueError:
                pass
        return {
            "arguments_sha256": sha256_hex(summary),
            "argument_keys": keys,
            "arguments": (
                _arguments(summary, self.cap) if self.settings.include_content else None
            ),
        }

    def _build(
        self, repo: Any, run_id: str, question: str | None, answer: str | None
    ) -> list[dict[str, Any]]:
        run = repo.get_run(run_id)
        if run is None:
            return []
        steps = repo.list_run_steps(run_id)
        step_by_call = {s.llm_call_id: s for s in steps if s.llm_call_id}
        base = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "turn_id": run.message_id,
            "tenant_id": self.tenant_id,
            "trace_id": _trace_id(),
            "conversation_id": run.conversation_id,
            "prompt_template_version": run.prompt_version,
            "run_status": run.status,
            # Lets consumers order the runs of a conversation (ids are random).
            "run_started_at": run.started_at,
        }
        include = self.settings.include_content
        records: list[dict[str, Any]] = []
        next_step = max([s.sequence for s in steps], default=0) + 1
        for call in run.metadata.get("llm_calls", []):
            call_id = call.get("llm_call_id")
            step = step_by_call.get(call_id)
            step_no = step.sequence if step else next_step
            if not step:
                next_step += 1
            role = call.get("role") or (
                _ROLE_BY_STEP.get(step.step_type, "answer") if step else "answer"
            )
            is_answer = role == "answer"
            usage_known = call.get("usage_known") is not False
            cost_usd, cost_kind, pricing_version = _cost(call)
            records.append(
                {
                    **base,
                    "record_type": RECORD_MODEL_CALL,
                    "call_id": call_id,
                    "step": step_no,
                    "role": role,
                    # Aliases/pricing/cost come from the provider result (#170).
                    "model_alias": call.get("model_alias"),
                    "provider_model_id": call.get("model_id"),
                    "input_tokens": _usage(call, "input_tokens"),
                    "output_tokens": _usage(call, "output_tokens"),
                    "reasoning_tokens": (
                        call.get("reasoning_tokens") if usage_known else None
                    ),
                    "ttft_ms": call.get("ttft_ms"),
                    "latency_ms": call.get("latency_ms"),
                    "finish_reason": call.get("finish_reason"),
                    "error": call.get("error_code"),
                    "fallback_used": bool(call.get("fallback_used")),
                    "cost_usd": cost_usd,
                    "cost_kind": cost_kind,
                    "pricing_version": pricing_version,
                    "tool_source": call.get("tool_source"),
                    "question_sha256": sha256_hex(question),
                    "answer_sha256": sha256_hex(answer) if is_answer else None,
                    "question": self.cap(question) if include else None,
                    "answer": self.cap(answer) if include and is_answer else None,
                }
            )
        for step in steps:
            if step.step_type != "tool_call":
                continue
            row_count = None
            summary = step.output_summary or ""
            if summary.startswith("rows="):
                try:
                    row_count = int(summary.split(",")[0][5:])
                except ValueError:
                    pass
            records.append(
                {
                    **base,
                    "record_type": RECORD_TOOL_CALL,
                    "call_id": step.tool_call_id or step.step_id,
                    "step": step.sequence,
                    "tool_name": step.tool_name,
                    "tool_call_id": step.tool_call_id,
                    **self._arguments_fields(step.input_summary),
                    "query_id": step.query_id,
                    "status": step.status,
                    "row_count": row_count,
                    "latency_ms": step.duration_ms,
                    "error": summary if step.status == "failed" else None,
                }
            )
        return records


def _arguments(summary: str | None, cap: Any) -> Any:
    if not summary:
        return None
    try:
        parsed = json.loads(summary)
    except ValueError:
        return cap(summary)
    return parsed if len(json.dumps(parsed)) <= 2048 else cap(summary)
