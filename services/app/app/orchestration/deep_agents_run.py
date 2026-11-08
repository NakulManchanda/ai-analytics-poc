"""Run lifecycle for ``AGENT_STRATEGY=deep_agents`` (#226).

A Deep Agent owns model and tool selection; this module wraps it in the same externally
visible run contract as ``OrchestrationLoop._execute_loop``: run events, RunStep rows, the
durable assistant Message, the Run row, LoopResult, run metrics, budgets, cancellation and
the terminal statuses/failure codes. ``_execute_loop`` is untouched; a few small cost
helpers are deliberately duplicated from it rather than refactored out of it.

Known differences from the manual loop are listed in ``docs``/the PR: the answer is not
streamed (``answer.completed`` only), no catalogue short-circuit, no voice synthesis, no
model-policy entitlement/per-run reservation (the proxy's guards still apply), ``write_todos``
calls produce no tool events.
"""

from __future__ import annotations

import concurrent.futures
import contextvars
import json
import logging
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx
from opentelemetry import propagate
from opentelemetry import trace as otel_trace

from app.config import LLMConfigurationError
from app.events import RunEvent, terminal_run_payload
from app.llm import AgentCallReservation, LiteLLMClient, LLMProviderError
from app.mcp_client import MCPToolError
from app.metrics import emit_run_metrics
from app.model_policy.pricing import PricingTable, default_pricing
from app.orchestration.budgets import BudgetExceededError, BudgetTracker
from app.orchestration.deep_agents_strategy import (
    MCP_TOOL_NAMES,
    PLANNING_TOOL_NAME,
    build_deep_agent,
)
from app.orchestration.loop import (
    BUDGET_BASIS_CONSERVATIVE,
    BUDGET_BASIS_REPORTED,
    BUDGET_BASIS_UNRESOLVABLE,
    COST_SOURCE_FALLBACK,
    COST_SOURCE_INCOMPLETE,
    COST_SOURCE_POLICY_ESTIMATE,
    COST_SOURCE_PROVIDER_BILLED,
    COST_SOURCE_UNPRICED,
    CallCost,
    LLMCall,
    LoopResult,
    OrchestrationError,
    RunCancelledError,
    _tool_error,
    _TracedCalls,
)
from app.state import (
    Message,
    Run,
    RunStep,
    generate_message_id,
    generate_step_id,
    generate_tool_call_id,
    utcnow_isoformat,
)
from app.validation import check_answer_grounding_multi, scan_observation

if TYPE_CHECKING:
    from app.orchestration.loop import OrchestrationLoop

try:  # langchain is an optional dependency of this strategy only
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError:  # pragma: no cover - dependency is declared in pyproject
    BaseCallbackHandler = object  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

STRATEGY_NAME = "deep_agents"
# LangGraph supersteps allowed per budgeted iteration (model node + tool node + middleware).
RECURSION_STEPS_PER_ITERATION = 4
_POLL_INTERVAL_SECONDS = 0.05
_NO_ANSWER_TEXT = (
    "The model did not return a usable response for that question. "
    "Please try again or rephrase it."
)


# Prior conversation context given to the Deep Agent on a follow-up turn (#226).
# Bounds are character-based proxies (~4 chars per token): the overall budget below is
# roughly 3k tokens. Every component of a turn is capped, not just the tool result.
HISTORY_MAX_TURNS = 4
HISTORY_MAX_TOTAL_CHARS = (
    12_000  # ~3k tokens; overall budget over all restored messages
)
HISTORY_USER_MAX_CHARS = 1_000
HISTORY_ASSISTANT_MAX_CHARS = 1_500
HISTORY_ARGUMENTS_MAX_CHARS = 500
HISTORY_TOOL_RESULT_MAX_CHARS = 2_000
HISTORY_MAX_OBSERVATIONS_PER_TURN = 2  # the most recent observations are kept
_TRUNCATED_MARKER = "[truncated]"
_NO_ANSWER_HISTORY_NOTE = "(No answer was recorded for this turn.)"
UNTRUSTED_TAG = "untrusted_tool_data"
_TAG_RE = re.compile(r"<(\s*/?\s*" + UNTRUSTED_TAG + ")", re.IGNORECASE)


@dataclass
class HistoryResult:
    """Restored history plus how much of it was bounded away (see ``metadata``)."""

    messages: list[dict[str, str]]
    metadata: dict[str, Any]


def _clip(text: str, cap: int) -> tuple[str, bool]:
    if len(text) <= cap:
        return text, False
    return text[: max(cap, 0)] + " " + _TRUNCATED_MARKER, True


def _neutralize(text: str) -> str:
    """Stop data from opening or closing an untrusted block: '<' becomes '&lt;'."""
    return _TAG_RE.sub(r"&lt;\1", text)


def _attr(value: object) -> str:
    return (
        _neutralize(str(value))
        .replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", " ")
    )


@dataclass
class _Obs:
    tool: str
    query_id: str | None
    arguments: str | None  # serialized JSON, uncapped
    content: str


@dataclass
class _Turn:
    user: str
    observations: list[_Obs]
    assistant: str


def _render_turn(
    turn: _Turn,
    *,
    user_cap: int,
    text_cap: int,
    args_cap: int,
    result_cap: int,
    max_obs: int,
) -> tuple[str, str, dict[str, int]]:
    counts = {
        "assistant_text": 0,
        "arguments": 0,
        "tool_results": 0,
        "observations_dropped": 0,
        "user_text": 0,
    }
    user, cut = _clip(turn.user, user_cap)
    counts["user_text"] += cut
    observations = turn.observations
    if len(observations) > max_obs:
        counts["observations_dropped"] += len(observations) - max_obs
        observations = (
            observations[len(observations) - max_obs :] if max_obs > 0 else []
        )
    blocks: list[str] = []
    for obs in observations:
        attrs = f'source="mcp" tool="{_attr(obs.tool)}"'
        if obs.query_id:
            attrs += f' query_id="{_attr(obs.query_id)}"'
        if obs.arguments is not None:
            args, cut = _clip(obs.arguments, args_cap)
            counts["arguments"] += cut
            attrs += f' arguments="{_attr(args)}"'
        content, cut = _clip(obs.content, result_cap)
        counts["tool_results"] += cut
        blocks.append(
            f"<{UNTRUSTED_TAG} {attrs}>\n{_neutralize(content)}\n</{UNTRUSTED_TAG}>"
        )
    answer, cut = _clip(turn.assistant, text_cap)
    counts["assistant_text"] += cut
    return user, "\n".join([*blocks, answer]), counts


def build_history_messages(
    repo: Any,
    conversation_id: str,
    current_message_id: str,
    *,
    max_turns: int = HISTORY_MAX_TURNS,
    max_total_chars: int = HISTORY_MAX_TOTAL_CHARS,
    max_user_chars: int = HISTORY_USER_MAX_CHARS,
    max_assistant_chars: int = HISTORY_ASSISTANT_MAX_CHARS,
    max_arguments_chars: int = HISTORY_ARGUMENTS_MAX_CHARS,
    max_tool_chars: int = HISTORY_TOOL_RESULT_MAX_CHARS,
    max_observations: int = HISTORY_MAX_OBSERVATIONS_PER_TURN,
) -> HistoryResult:
    """Prior turns of one conversation as plain, strictly alternating user/assistant
    messages (no tool-role messages: provider tool-call pairing rules reject stored tool
    messages replayed without call ids). Each stored tool observation is folded into the
    assistant message of its turn inside an ``<untrusted_tool_data ...>`` block carrying its
    provenance (tool, query_id, arguments), placed before the assistant's own answer. The
    current turn's user message (and anything after it) is excluded. The result is empty or
    starts with a user message and ends with an assistant message.

    Bounds: turns are taken newest to oldest and kept only while the total character budget
    allows (oldest dropped first); user text, assistant text, serialized arguments, each tool
    result and the observations per turn (most recent kept) are individually capped. The
    newest turn is always present: if it alone exceeds the budget its tool results, then
    arguments, then assistant text are shrunk further. ``total_chars`` therefore stays within
    ``budget_chars`` except when even the fully shrunk newest turn cannot fit; that floor is
    the capped user text plus the block wrappers/markers of at most ``max_observations``
    observations (tens of characters each, plus the capped tool/query names)."""
    stored = repo.list_messages(conversation_id)
    cutoff = next(
        (m.sequence for m in stored if m.message_id == current_message_id), None
    )
    # entries: [role, user_or_assistant_text, observations]
    merged: list[list[Any]] = []

    def push(role: str, text: str, obs: list[_Obs]) -> None:
        if merged and merged[-1][0] == role:
            merged[-1][1] += "\n\n" + text
            merged[-1][2] = [*merged[-1][2], *obs]
        else:
            merged.append([role, text, list(obs)])

    pending: list[_Obs] = []
    for message in stored:
        if message.conversation_id != conversation_id:
            continue
        if message.message_id == current_message_id or (
            cutoff is not None and message.sequence >= cutoff
        ):
            continue
        if message.role == "tool":
            arguments = message.metadata.get("arguments")
            pending.append(
                _Obs(
                    tool=str(message.metadata.get("tool_name") or "tool"),
                    query_id=message.metadata.get("query_id"),
                    arguments=(
                        json.dumps(dict(arguments), sort_keys=True, default=str)
                        if isinstance(arguments, Mapping)
                        else None
                    ),
                    content=message.content,
                )
            )
        elif message.role == "assistant":
            push("assistant", message.content, pending)
            pending = []
        elif message.role == "user":
            # Consecutive user messages (a failed earlier turn) are merged by push().
            if pending:  # earlier run ended before answering
                push("assistant", _NO_ANSWER_HISTORY_NOTE, pending)
                pending = []
            push("user", message.content, [])
    if pending:
        push("assistant", _NO_ANSWER_HISTORY_NOTE, pending)
    while merged and merged[0][0] != "user":
        merged.pop(0)
    if merged and merged[-1][0] == "user":
        merged.append(["assistant", _NO_ANSWER_HISTORY_NOTE, []])
    turns = [
        _Turn(merged[i][1], merged[i + 1][2], merged[i + 1][1])
        for i in range(0, len(merged) - 1, 2)
    ]

    caps = {
        "user_cap": max_user_chars,
        "text_cap": max_assistant_chars,
        "args_cap": max_arguments_chars,
        "result_cap": max_tool_chars,
        "max_obs": max_observations,
    }

    def size(rendered: tuple[str, str, dict[str, int]]) -> int:
        return len(rendered[0]) + len(rendered[1])

    included: list[tuple[str, str, dict[str, int]]] = []  # newest first
    total = 0
    for turn in reversed(turns[-max_turns:] if max_turns > 0 else []):
        rendered = _render_turn(turn, **caps)
        if not included and total + size(rendered) > max_total_chars:
            # The newest turn is always kept: shrink result, then arguments, then text.
            shrunk = dict(caps)
            for key in ("result_cap", "args_cap", "text_cap"):
                if size(_render_turn(turn, **{**shrunk, key: 0})) <= max_total_chars:
                    lo, hi = 0, shrunk[key]  # largest cap that fits (size is monotonic)
                    while lo < hi:
                        mid = (lo + hi + 1) // 2
                        if (
                            size(_render_turn(turn, **{**shrunk, key: mid}))
                            <= max_total_chars
                        ):
                            lo = mid
                        else:
                            hi = mid - 1
                    shrunk[key] = lo
                    break
                shrunk[key] = 0
            rendered = _render_turn(turn, **shrunk)
        elif total + size(rendered) > max_total_chars:
            break
        included.append(rendered)
        total += size(rendered)
    included.reverse()

    truncated = {
        "assistant_text": 0,
        "arguments": 0,
        "tool_results": 0,
        "observations_dropped": 0,
        "user_text": 0,
    }
    for _u, _a, counts in included:
        for key, value in counts.items():
            truncated[key] += value
    messages: list[dict[str, str]] = []
    for user, assistant, _counts in included:
        messages.append({"role": "user", "content": user})
        messages.append({"role": "assistant", "content": assistant})
    return HistoryResult(
        messages=messages,
        metadata=history_metadata(
            turns_available=len(turns),
            turns_included=len(included),
            total_chars=total,
            budget_chars=max_total_chars,
            truncated=truncated,
        ),
    )


def history_metadata(
    *,
    turns_available: int = 0,
    turns_included: int = 0,
    total_chars: int = 0,
    budget_chars: int = HISTORY_MAX_TOTAL_CHARS,
    truncated: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "turns_available": turns_available,
        "turns_included": turns_included,
        "turns_dropped": turns_available - turns_included,
        "total_chars": total_chars,
        "budget_chars": budget_chars,
        "truncated": truncated
        or {
            "assistant_text": 0,
            "arguments": 0,
            "tool_results": 0,
            "observations_dropped": 0,
            "user_text": 0,
        },
    }


class _Aborted(BaseException):
    """Raised inside the abandoned worker once the run already reached a terminal state."""


# LangChain run id of the model call this thread is about to issue; set by the recorder's
# on_chat_model_start (synchronous, same thread/context as the request that follows).
_current_lc_run_id: contextvars.ContextVar[UUID | None] = contextvars.ContextVar(
    "deep_agents_lc_run_id", default=None
)


class _Correlation:
    """httpx hooks giving every proxy request the same correlation the manual LiteLLM
    client sends (run/call/eval ids, session, trace context), capturing the proxy's billed
    cost per request, and refusing/bounding requests once the run is over.

    Association is per request: the recorder registers ``lc_run_id -> call_id`` and sets a
    ContextVar; the request hook reads it and stamps ``request.extensions['lc_run_id']``;
    the response hook files the billed cost under that id. A request with no run id in
    context carries no call_id and no cost is attributed to it.
    """

    def __init__(
        self,
        run_id: str,
        conversation_id: str,
        eval_run_id: str | None,
        *,
        abort: threading.Event | None = None,
        remaining_seconds: Callable[[], float | None] | None = None,
    ):
        self.run_id = run_id
        self.conversation_id = conversation_id
        self.eval_run_id = eval_run_id
        self.abort = abort or threading.Event()
        self._remaining = remaining_seconds
        self._lock = threading.Lock()
        self._call_ids: dict[UUID, str] = {}
        self._costs: dict[UUID, float] = {}
        self.header_failures = 0
        self.cost_header_missing = 0

    def snapshot(
        self, *, billed_calls: int = 0, estimated_calls: int = 0
    ) -> dict[str, Any]:
        """Correlation health for run metadata.

        ``correlation_ok`` is header integrity only: failures make the run's proxy-side
        attribution unreliable. ``billing_complete`` is separate and stricter: header
        health is clean, no billed-cost header was missing or unparseable, and no recorded
        LLM call was priced from the policy-table estimate (``estimated_calls == 0``), i.e.
        every recorded call carries a provider-billed cost. Callers needing measured cost
        (#228 cost comparisons) must NOT treat a run with ``billing_complete == False`` as a
        trustworthy cost measurement.

        A run with zero recorded LLM calls (failed or cancelled before the first model
        call) is vacuously complete: there is nothing to bill. A request that was still in
        flight when a run was abandoned is not recorded at all; that gap is tracked
        separately (billed-cost reconciliation after cancellation, #226).
        """
        with self._lock:
            billing_complete = (
                self.header_failures == 0
                and self.cost_header_missing == 0
                and estimated_calls == 0
            )
            return {
                "correlation": {
                    "header_failures": self.header_failures,
                    "cost_header_missing": self.cost_header_missing,
                    "billing_complete": billing_complete,
                    "billed_calls": billed_calls,
                    "estimated_calls": estimated_calls,
                },
                "correlation_ok": self.header_failures == 0,
                "billing_complete": billing_complete,
            }

    def register(self, lc_run_id: UUID, call_id: str) -> None:
        with self._lock:
            self._call_ids[lc_run_id] = call_id
        _current_lc_run_id.set(lc_run_id)

    def release(self, lc_run_id: UUID) -> float | None:
        """Forget a finished call and return its billed cost, if the proxy reported one."""
        _current_lc_run_id.set(None)
        with self._lock:
            self._call_ids.pop(lc_run_id, None)
            return self._costs.pop(lc_run_id, None)

    def check_abort(self) -> None:
        if self.abort.is_set():
            raise _Aborted()

    def on_request(self, request: httpx.Request) -> None:
        self.check_abort()  # never send once the run is over
        if self._remaining is not None:
            remaining = self._remaining()
            if remaining is not None:
                if remaining <= 0:
                    raise _Aborted()
                current = request.extensions.get("timeout") or {}
                request.extensions["timeout"] = {
                    key: (
                        remaining
                        if current.get(key) is None
                        else min(current[key], remaining)
                    )
                    for key in ("connect", "read", "write", "pool")
                }
        try:
            lc_run_id = _current_lc_run_id.get()
            with self._lock:
                call_id = self._call_ids.get(lc_run_id) if lc_run_id else None
            meta: dict[str, Any] = {"run_id": self.run_id}
            if call_id is not None:
                meta["call_id"] = call_id
                request.extensions["lc_run_id"] = lc_run_id
            meta["attempt"] = 1
            if self.eval_run_id:
                meta["eval_run_id"] = self.eval_run_id
            headers = request.headers
            headers["x-litellm-spend-logs-metadata"] = json.dumps(
                meta, separators=(",", ":"), ensure_ascii=True
            )
            if self.eval_run_id:
                headers["x-litellm-tags"] = f"eval_run_id:{self.eval_run_id}"
            headers["x-conversation-id"] = self.conversation_id
            headers["x-litellm-session-id"] = self.conversation_id
            carrier: dict[str, str] = {}
            propagate.inject(carrier)
            for key, value in carrier.items():
                headers[key] = value
        except Exception:
            with self._lock:
                self.header_failures += 1
            logger.warning("deep_agents_correlation_headers_failed", exc_info=True)

    def on_response(self, response: httpx.Response) -> None:
        lc_run_id = response.request.extensions.get("lc_run_id")
        if lc_run_id is None:
            return
        try:
            cost = float(response.headers.get("x-litellm-response-cost", ""))
        except (TypeError, ValueError):
            with self._lock:
                self.cost_header_missing += 1
            return
        with self._lock:
            self._costs[lc_run_id] = cost


class _Emitter:
    """Sequenced run events (same envelope as ``_execute_loop``'s ``emit``)."""

    def __init__(self, loop: OrchestrationLoop, run_id, conv_id, eval_run_id, seq):
        self._publisher = loop._publisher
        self._run_id = run_id
        self._conv_id = conv_id
        self._eval_run_id = eval_run_id
        self._seq = seq
        self._lock = threading.Lock()

    def __call__(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        llm_call_id: str | None = None,
        tool_call_id: str | None = None,
        query_id: str | None = None,
    ) -> None:
        if self._publisher is None:
            return
        with self._lock:
            event = RunEvent(
                event_type=event_type,
                run_id=self._run_id,
                conversation_id=self._conv_id,
                sequence=self._seq,
                payload=payload or {},
                llm_call_id=llm_call_id,
                tool_call_id=tool_call_id,
                query_id=query_id,
                eval_run_id=self._eval_run_id,
            )
            self._publisher.publish(event)
            self._seq += 1


def _messages_json(batches: Any) -> str:
    return json.dumps(
        [[{"role": m.type, "content": m.content} for m in batch] for batch in batches],
        default=str,
    )


def _tools_json(invocation_params: Any) -> str:
    """Serialized tool schemas LangChain bound to this request.

    ``on_chat_model_start`` receives ``kwargs['invocation_params']['tools']``, verified
    identical to the ``tools`` array in the outbound request body, so the reservation
    covers the complete prompt (messages + tool definitions), not messages alone.
    """
    tools = (invocation_params or {}).get("tools") or []
    return json.dumps(tools, default=str) if tools else ""


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            (
                b
                if isinstance(b, str)
                else b.get("text", "") if isinstance(b, dict) else ""
            )
            for b in content
        )
    return ""


class _RunRecorder(BaseCallbackHandler):  # type: ignore[misc,valid-type]
    """Turns LangChain model/tool callbacks into the manual loop's events, steps, LLMCall
    records and budget accounting. Exceptions propagate (``raise_error``) so budget and
    cancellation errors stop the graph."""

    raise_error = True
    run_inline = True

    def __init__(
        self,
        *,
        loop: OrchestrationLoop,
        llm: LiteLLMClient,
        tracker: BudgetTracker,
        emit: _Emitter,
        correlation: _Correlation,
        run_id: str,
        conv_id: str,
        prompt: str,
        next_seq: int,
    ) -> None:
        self.loop = loop
        self.llm = llm
        self.tracker = tracker
        self.emit = emit
        self.correlation = correlation
        self.run_id = run_id
        self.conv_id = conv_id
        self.prompt = prompt
        self.next_seq = next_seq
        self.lock = threading.RLock()
        self.abort = correlation.abort
        self.steps: list[RunStep] = []
        self.llm_calls: list[LLMCall] = _TracedCalls()
        self.step_seq = 1
        self.executed_signatures: set[str] = set()
        self.last_tool_call_id: str | None = None
        self.last_query_id: str | None = None
        self.last_tool_name: str | None = None
        self.proposal_latency_ms = 0
        self.tool_latency_ms: int | None = None
        self.final_latency_ms: int | None = None
        self.last_failed_call_id: str | None = None
        self._calls: dict[UUID, tuple[str, float]] = {}
        self.agent_alias = llm.planner_alias
        self.agent_max_tokens: int | None = None
        self._holds: dict[UUID, AgentCallReservation] = {}
        self._tools: dict[UUID, dict[str, Any]] = {}
        self.tools_succeeded = 0
        self.tools_failed = 0
        self.evidence: list[dict[str, Any]] = []

    # -- helpers -------------------------------------------------------------------
    def _guard(self) -> None:
        self.correlation.check_abort()
        if self.loop.is_cancelled(self.run_id):
            raise RunCancelledError()
        self.tracker.check_deadline()

    def _add_step(self, **fields: Any) -> RunStep:
        step = RunStep(
            step_id=generate_step_id(),
            run_id=self.run_id,
            sequence=self.step_seq,
            **fields,
        )
        self.loop._repo.add_run_step(step)
        self.steps.append(step)
        self.step_seq += 1
        return step

    def _priced_key(
        self, result: object, model_id: str | None
    ) -> tuple[PricingTable, str] | None:
        alias = getattr(result, "model_alias", None)
        model_id = getattr(result, "model_id", None) or model_id
        from app.config import DEFAULT_MODEL_ID

        for table in (self.llm.pricing, default_pricing((DEFAULT_MODEL_ID,))):
            if not isinstance(table, PricingTable):
                continue
            for key in (alias, model_id):
                if key and key in table.rates:
                    return table, key
        return None

    def _call_cost(
        self, input_tokens: int, output_tokens: int, result: object
    ) -> CallCost:
        """Duplicate of ``_execute_loop.call_cost`` minus the self-hosted branch."""
        budgets = self.tracker.budgets
        result_cost = getattr(result, "cost_usd", None)
        authoritative = (
            getattr(result, "cost_kind", None) == "billed"
            or getattr(result, "usage_known", True) is not False
        )
        if result_cost is not None and authoritative:
            source = (
                COST_SOURCE_PROVIDER_BILLED
                if getattr(result, "cost_kind", None) == "billed"
                else COST_SOURCE_POLICY_ESTIMATE
            )
            return CallCost(
                float(result_cost), source, float(result_cost), BUDGET_BASIS_REPORTED
            )
        incomplete = (
            input_tokens <= 0
            or output_tokens <= 0
            or getattr(result, "usage_known", True) is False
        )
        priced = self._priced_key(result, None)
        if priced is None:
            return CallCost(0.0, COST_SOURCE_UNPRICED, None, BUDGET_BASIS_UNRESOLVABLE)
        table, key = priced
        estimate = table.estimate(key, input_tokens, output_tokens) or 0.0
        if not incomplete:
            return CallCost(
                estimate, COST_SOURCE_FALLBACK, estimate, BUDGET_BASIS_REPORTED
            )
        bound = table.estimate(key, budgets.max_input_tokens, budgets.max_output_tokens)
        return CallCost(
            estimate,
            COST_SOURCE_INCOMPLETE,
            max(estimate, bound or 0.0),
            BUDGET_BASIS_CONSERVATIVE,
        )

    @staticmethod
    def _cost_fields(cost: CallCost) -> dict[str, Any]:
        return {
            "cost_usd": cost.cost_usd,
            "cost_source": cost.source,
            "budget_cost_usd": cost.budget_cost_usd,
            "budget_cost_basis": cost.budget_basis,
            "cost_indeterminate": cost.indeterminate,
        }

    def _record_cost(self, input_tokens: int, output_tokens: int, cost: CallCost):
        self.tracker.record_llm_call(
            input_tokens,
            output_tokens,
            cost.cost_usd,
            budget_cost_usd=cost.budget_cost_usd,
            cost_indeterminate=cost.indeterminate,
            cost_unresolvable=cost.budget_cost_usd is None,
        )

    # -- model calls ---------------------------------------------------------------
    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):  # type: ignore[no-untyped-def]
        with self.lock:
            self._guard()
            tracker = self.tracker
            if tracker.llm_call_count >= tracker.budgets.max_llm_calls:
                raise BudgetExceededError(
                    "max_llm_calls",
                    {
                        "limit": tracker.budgets.max_llm_calls,
                        "current": tracker.llm_call_count,
                    },
                )
            tracker.ensure_cost_determinate()
            # App-side monthly Bedrock allowance, as in LiteLLMClient._prepare_request.
            self.llm._reserve_budget()
            # Model policy (per-run reservation); fallbacks are not applied here.
            # Reserve for messages AND the bound tool schemas (both are input tokens).
            # Commit still does not cap actual usage above the reservation.
            self._holds[run_id] = self.llm.begin_agent_call(
                _messages_json(messages),
                tools_text=_tools_json(kwargs.get("invocation_params")),
            )
            call_id = self.loop._llm_call_id_factory()
            self._calls[run_id] = (call_id, self.loop._monotonic())
            self.correlation.register(run_id, call_id)
            self.emit(
                "llm.started",
                {"llm_call_id": call_id, "phase": "agent_step"},
                llm_call_id=call_id,
            )

    def on_llm_end(self, response, *, run_id, **kwargs):  # type: ignore[no-untyped-def]
        with self.lock:
            self._guard()
            call_id, started = self._calls.pop(run_id)
            hold = self._holds.pop(run_id, None)
            latency_ms = int((self.loop._monotonic() - started) * 1000)
            message = response.generations[0][0].message
            usage = getattr(message, "usage_metadata", None) or {}
            usage_known = bool(usage)
            input_tokens = int(usage.get("input_tokens", 0) or 0)
            output_tokens = int(usage.get("output_tokens", 0) or 0)
            meta = getattr(message, "response_metadata", None) or {}
            alias = self.agent_alias
            model_id = meta.get("model_name") or alias
            billed = self.correlation.release(run_id)
            result = SimpleNamespace(
                model_alias=alias,
                model_id=model_id,
                usage_known=usage_known,
                cost_usd=billed,
                cost_kind="billed" if billed is not None else None,
            )
            if hold is not None:
                self.llm.end_agent_call(
                    hold,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    usage_known=usage_known,
                    cost_usd=billed,
                )
            cost = self._call_cost(input_tokens, output_tokens, result)
            self._record_cost(input_tokens, output_tokens, cost)
            tool_calls = getattr(message, "tool_calls", None) or []
            call = LLMCall(
                llm_call_id=call_id,
                model_id=model_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=latency_ms,
                finish_reason=meta.get("finish_reason"),
                **self._cost_fields(cost),
                tool_source="model",
                model_alias=alias,
                role="planner",
                pricing_version=self.llm.pricing.version,
                result_cost_usd=billed,
                cost_kind=result.cost_kind,
                usage_known=usage_known,
                configured_max_tokens=self.agent_max_tokens,
                fallback_used=False,
            )
            self.llm_calls.append(call)
            self.emit(
                "llm.completed",
                {
                    "llm_call_id": call_id,
                    "phase": "agent_step",
                    "latency_ms": latency_ms,
                    "tokens": {"input": input_tokens, "output": output_tokens},
                },
                llm_call_id=call_id,
            )
            if tool_calls:
                self.proposal_latency_ms += latency_ms
                names = ",".join(str(tc.get("name")) for tc in tool_calls)
                self._add_step(
                    step_type="llm_proposal",
                    status="completed",
                    llm_call_id=call_id,
                    input_summary=f"prompt: {self.prompt[:80]}",
                    output_summary=f"tool: {names}",
                    duration_ms=latency_ms,
                    metadata={"tool_source": "model", **call.to_metadata()},
                )
            else:
                self.final_latency_ms = latency_ms
                self._add_step(
                    step_type="llm_final_answer",
                    status="completed",
                    llm_call_id=call_id,
                    input_summary=f"question: {self.prompt[:80]}",
                    output_summary=f"answer: {_message_text(message)[:80]}",
                    duration_ms=latency_ms,
                    metadata=call.to_metadata(),
                )

    def on_llm_error(self, error, *, run_id, **kwargs):  # type: ignore[no-untyped-def]
        with self.lock:
            entry = self._calls.pop(run_id, None)
            self.llm.release_agent_call(self._holds.pop(run_id, None))
            self.correlation.release(run_id)
            if entry is None or self.abort.is_set():
                return
            call_id, started = entry
            self.last_failed_call_id = call_id
            code = _provider_failure(error).code if _is_provider(error) else None
            if code is None:
                return  # budget/cancel/internal errors are not provider failures
            alias = self.agent_alias
            result = SimpleNamespace(
                model_alias=alias, model_id=alias, usage_known=False
            )
            cost = self._call_cost(0, 0, result)
            self.llm_calls.append(
                LLMCall(
                    llm_call_id=call_id,
                    model_id=alias,
                    input_tokens=None,
                    output_tokens=None,
                    latency_ms=int((self.loop._monotonic() - started) * 1000),
                    **{**self._cost_fields(cost), "cost_usd": None},
                    error_code=code,
                    tool_source="model",
                    model_alias=alias,
                    role="planner",
                    usage_known=False,
                    configured_max_tokens=self.agent_max_tokens,
                    provider_status=getattr(error, "status_code", None),
                )
            )
            try:
                self._record_cost(0, 0, cost)
            except BudgetExceededError:
                pass  # keep the provider failure code, as the manual loop does

    def release_open_holds(self) -> None:
        """Return run-budget holds of calls still open when the run ends."""
        with self.lock:
            holds, self._holds = list(self._holds.values()), {}
        for hold in holds:
            self.llm.release_agent_call(hold)

    # -- tool calls ----------------------------------------------------------------
    @staticmethod
    def _plan_updated(inputs: Any, input_str: Any) -> None:
        """``ai.plan.updated`` span event with todo COUNTS only (never the todo text)."""
        try:
            if inputs is None:
                inputs = json.loads(input_str)
            todos = inputs.get("todos") if isinstance(inputs, Mapping) else None
            if not isinstance(todos, list):
                return
            statuses = [
                t.get("status") if isinstance(t, Mapping) else None for t in todos
            ]
            otel_trace.get_current_span().add_event(
                "ai.plan.updated",
                {
                    "todos_total": len(todos),
                    "pending": statuses.count("pending"),
                    "in_progress": statuses.count("in_progress"),
                    "completed": statuses.count("completed"),
                },
            )
        except Exception:  # observability never fails the run
            logger.warning("plan_event_failed", exc_info=True)

    def on_tool_start(self, serialized, input_str, *, run_id, inputs=None, **kwargs):  # type: ignore[no-untyped-def]
        name = (serialized or {}).get("name") or kwargs.get("name")
        if name == PLANNING_TOOL_NAME:
            self._plan_updated(inputs, input_str)
            return
        with self.lock:
            self._guard()
            if inputs is None:
                try:
                    inputs = json.loads(input_str)
                except (TypeError, ValueError):
                    inputs = {}
            arguments = dict(inputs) if isinstance(inputs, Mapping) else {}
            self.emit("tool.requested", {"tool_name": name, **arguments})
            signature = f"{name}:{json.dumps(arguments, sort_keys=True, default=str)}"
            if signature in self.executed_signatures:
                raise BudgetExceededError(
                    f"Repeated equivalent tool call detected: {signature}",
                    {"limit": "repeated_tool_call", "signature": signature},
                )
            self.executed_signatures.add(signature)
            tool_call_id = generate_tool_call_id()
            self.last_tool_call_id = tool_call_id
            self.last_tool_name = name
            self._tools[run_id] = {
                "name": name,
                "arguments": arguments,
                "tool_call_id": tool_call_id,
                "started": self.loop._monotonic(),
            }
            self.emit(
                "tool.started",
                {"tool_call_id": tool_call_id, "tool_name": name},
                tool_call_id=tool_call_id,
            )

    def on_tool_end(self, output, *, run_id, **kwargs):  # type: ignore[no-untyped-def]
        with self.lock:
            info = self._tools.pop(run_id, None)
            if info is None:
                return
            self._guard()
            text = _message_text(output)
            duration_ms = int((self.loop._monotonic() - info["started"]) * 1000)
            self.tool_latency_ms = (self.tool_latency_ms or 0) + duration_ms
            try:
                result = json.loads(text)
            except ValueError:
                result = None
            if isinstance(result, dict) and "error" in result:
                self._tool_failed(info, result, text, duration_ms)
                return
            query_result = result if isinstance(result, dict) else {"value": result}
            query_id = str(query_result.get("query_id", ""))
            self.last_query_id = query_id
            result_bytes = len(text.encode("utf-8"))
            self.tracker.record_tool_call(result_bytes=result_bytes)
            self.tools_succeeded += 1
            self.evidence.append(query_result)
            row_count = query_result.get("row_count", 0)
            tool_call_id = info["tool_call_id"]
            self.emit(
                "tool.completed",
                {
                    "tool_call_id": tool_call_id,
                    "query_id": query_id,
                    "row_count": row_count,
                    "duration_ms": duration_ms,
                },
                tool_call_id=tool_call_id,
                query_id=query_id,
            )
            self._add_step(
                step_type="tool_call",
                status="completed",
                tool_name=info["name"],
                tool_call_id=tool_call_id,
                query_id=query_id,
                input_summary=json.dumps(
                    info["arguments"], sort_keys=True, default=str
                ),
                output_summary=f"rows={row_count}, bytes={result_bytes}",
                duration_ms=duration_ms,
            )
            findings = scan_observation(query_result)
            if findings:
                self._add_step(
                    step_type="observation_scan",
                    status="completed",
                    query_id=query_id,
                    output_summary="instruction-like text in tool observation",
                    metadata={"findings": [f.to_dict() for f in findings]},
                )
            self.loop._repo.add_message(
                Message(
                    message_id=generate_message_id(),
                    conversation_id=self.conv_id,
                    sequence=self.next_seq,
                    role="tool",
                    content=json.dumps(
                        query_result, separators=(",", ":"), sort_keys=True
                    ),
                    metadata={
                        "tool_name": info["name"],
                        "query_id": query_id,
                        "arguments": info["arguments"],
                    },
                )
            )
            self.next_seq += 1

    def on_tool_error(self, error, *, run_id, **kwargs):  # type: ignore[no-untyped-def]
        with self.lock:
            info = self._tools.pop(run_id, None)
            if info is None or self.abort.is_set():
                return
            duration_ms = int((self.loop._monotonic() - info["started"]) * 1000)
            self._tool_failed(
                info,
                {"error": "tool_failed", "reason": str(error)},
                str(error),
                duration_ms,
            )

    def _tool_failed(
        self, info: dict[str, Any], result: dict[str, Any], text: str, duration_ms: int
    ) -> None:
        """The model received the error object and may react; the run continues."""
        self.tools_failed += 1
        reason = str(result.get("reason") or result.get("error"))
        message = str(result.get("message") or result.get("error") or text)[:200]
        tool_call_id = info["tool_call_id"]
        self.emit(
            "tool.failed",
            {
                "tool_call_id": tool_call_id,
                "tool_name": info["name"],
                "error": message,
                "reason": reason,
                "duration_ms": duration_ms,
            },
            tool_call_id=tool_call_id,
        )
        self._add_step(
            step_type="tool_call",
            status="failed",
            tool_name=info["name"],
            tool_call_id=tool_call_id,
            input_summary=json.dumps(info["arguments"], sort_keys=True, default=str),
            output_summary=f"error: {message}",
            duration_ms=duration_ms,
            metadata={"reason": reason},
        )
        self.tracker.record_tool_call(result_bytes=0)


# -- provider error mapping --------------------------------------------------------
def _is_provider(error: BaseException) -> bool:
    import openai

    return isinstance(error, (openai.OpenAIError, LLMProviderError))


def _provider_failure(error: BaseException) -> OrchestrationError | Any:
    """Map an SDK/provider failure to the manual loop's ``litellm_proxy_*`` codes."""
    import openai

    if isinstance(error, LLMProviderError):
        return SimpleNamespace(code=error.code, retryable=error.retryable, details=None)
    if isinstance(error, openai.APIStatusError):
        status = error.status_code
        code = f"litellm_proxy_http_{status}"
        return SimpleNamespace(
            code=code,
            retryable=status in (503, 504, 529),
            details={
                "category": code,
                "provider_status": status,
                "provider_call_id": error.response.headers.get("x-litellm-call-id"),
                "attempts": 1,
            },
        )
    if isinstance(error, openai.APIConnectionError):  # includes timeouts
        return SimpleNamespace(
            code="litellm_proxy_unavailable", retryable=True, details=None
        )
    return SimpleNamespace(
        code="litellm_proxy_request_error", retryable=False, details=None
    )


# -- run ---------------------------------------------------------------------------
def execute_deep_agent_run(
    loop: OrchestrationLoop,
    *,
    prompt: str,
    conv_id: str,
    user_msg_id: str,
    run_id: str,
    next_seq: int,
    initial_evt_sequence: int = 1,
    budgets: Any | None = None,
) -> LoopResult:
    """Drop-in replacement for ``OrchestrationLoop._execute_loop`` (same arguments)."""
    active_budgets = budgets or loop._budgets
    tracker = BudgetTracker(budgets=active_budgets)
    start_mono = loop._monotonic()
    run = loop._repo.get_run(run_id)
    assert run is not None, f"Run {run_id} not found; prepare_run must be called first"
    eval_run_id: str | None = run.metadata.get("eval_run_id")
    eval_meta: dict[str, Any] = {"eval_run_id": eval_run_id} if eval_run_id else {}
    emit = _Emitter(loop, run_id, conv_id, eval_run_id, initial_evt_sequence)
    abort = threading.Event()
    correlation = _Correlation(
        run_id,
        conv_id,
        eval_run_id,
        abort=abort,
        remaining_seconds=lambda: active_budgets.timeout_seconds
        - tracker.elapsed_seconds,
    )
    # Trace health at run start: an invalid context means no exporter/tracer is wired (the
    # proxy and MCP then receive no traceparent); recorded, never fatal.
    span_context = otel_trace.get_current_span().get_span_context()
    trace_valid = span_context.is_valid
    trace_id = format(span_context.trace_id, "032x") if trace_valid else None
    recorder: _RunRecorder | None = None
    history = HistoryResult([], history_metadata())
    configured_model_id = ""
    pending_llm_call_id = ""
    grounding_report: dict[str, Any] = {}

    def telemetry(end_to_end_latency_ms: int) -> dict[str, Any]:
        rec = recorder
        used_tool = rec is not None and rec.last_tool_name is not None
        return {
            "end_to_end_latency_ms": end_to_end_latency_ms,
            "proposal_llm_latency_ms": rec.proposal_latency_ms if rec else None,
            "tool_latency_ms": rec.tool_latency_ms if rec else None,
            "final_answer_llm_latency_ms": rec.final_latency_ms if rec else None,
            "ttft": {"available": False, "reason": "non_streaming_blocking"},
            "tool_source": "model",
            "planner_outcome": "tool_call" if used_tool else "answer",
            "selected_tool": rec.last_tool_name if rec else None,
            "rejected_outcome": None,
            "offered_tools": [*MCP_TOOL_NAMES, PLANNING_TOOL_NAME],
            "agent_strategy": STRATEGY_NAME,
        }

    def calls() -> list[LLMCall]:
        return recorder.llm_calls if recorder else []

    def current_model_id() -> str:
        return calls()[-1].model_id if calls() else configured_model_id

    def current_cost_source() -> str:
        return calls()[-1].cost_source if calls() else COST_SOURCE_FALLBACK

    def evidence() -> dict[str, Any]:
        ok = recorder.tools_succeeded if recorder else 0
        return {
            "tool_calls_succeeded": ok,
            "tool_calls_failed": recorder.tools_failed if recorder else 0,
            "evidence_backed": ok > 0,
        }

    def run_metadata(extra: dict[str, Any], tel: dict[str, Any]) -> dict[str, Any]:
        return {
            **extra,
            "telemetry": tel,
            "strategy": STRATEGY_NAME,
            "grounding": "applied" if grounding_report else "not_applied",
            **({"grounding_report": grounding_report} if grounding_report else {}),
            "evidence": evidence(),
            **eval_meta,
            "trace": {
                "context_valid": trace_valid,
                "trace_id": trace_id,
                "propagation_failures": correlation.header_failures,
            },
            **correlation.snapshot(
                billed_calls=sum(1 for c in calls() if c.cost_kind == "billed"),
                estimated_calls=sum(1 for c in calls() if c.cost_kind != "billed"),
            ),
            "history": history.metadata,
            "cost_source": current_cost_source(),
            "llm_calls": [call.to_metadata() for call in calls()],
        }

    def finish(
        status: str,
        event_type: str,
        tel: dict[str, Any],
        *,
        failure_code: str | None,
        metadata: dict[str, Any],
        payload_extra: dict[str, Any] | None = None,
        event_llm_call_id: str | None = None,
    ) -> None:
        loop._repo.update_run(
            Run(
                run_id=run_id,
                conversation_id=conv_id,
                message_id=user_msg_id,
                status=status,
                model=current_model_id(),
                prompt_version="m9.v1",
                started_at=run.started_at,
                completed_at=utcnow_isoformat(),
                input_tokens=tracker.input_tokens,
                output_tokens=tracker.output_tokens,
                estimated_cost_usd=tracker.estimated_cost_usd,
                failure_code=failure_code,
                metadata=run_metadata(metadata, tel),
            )
        )
        emit(
            event_type,
            terminal_run_payload(
                status=status,
                input_tokens=tracker.input_tokens,
                output_tokens=tracker.output_tokens,
                estimated_cost_usd=tracker.estimated_cost_usd,
                failure_code=failure_code,
                telemetry=tel,
                **(payload_extra or {}),
            ),
            llm_call_id=event_llm_call_id,
        )
        emit_run_metrics(
            run_id=run_id,
            conversation_id=conv_id,
            milestone="v3.1",
            model=current_model_id(),
            turn_type="text",
            status=status,
            telemetry=tel,
            input_tokens=tracker.input_tokens,
            output_tokens=tracker.output_tokens,
            estimated_cost_usd=tracker.estimated_cost_usd,
            cost_source=current_cost_source(),
            llm_calls=[call.to_metadata() for call in calls()],
        )

    def result_for(
        status: str, answer: str, tel: dict[str, Any], latency_ms: int, code=None
    ) -> LoopResult:
        rec = recorder
        return LoopResult(
            answer=answer,
            status=status,
            run_id=run_id,
            conversation_id=conv_id,
            steps=rec.steps if rec else [],
            tool_call_id=rec.last_tool_call_id if rec else None,
            query_id=rec.last_query_id if rec else None,
            input_tokens=tracker.input_tokens,
            output_tokens=tracker.output_tokens,
            total_tokens=tracker.total_tokens,
            estimated_cost_usd=tracker.estimated_cost_usd,
            latency_ms=latency_ms,
            failure_code=code,
            llm_calls=calls(),
            telemetry=tel,
        )

    executor: concurrent.futures.ThreadPoolExecutor | None = None
    http_client: httpx.Client | None = None
    try:
        if loop.is_cancelled(run_id):
            raise RunCancelledError()
        pending_llm_call_id = loop._llm_call_id_factory()
        try:
            llm = loop._get_llm_client()
            if not isinstance(llm, LiteLLMClient):
                raise LLMConfigurationError(
                    "AGENT_STRATEGY=deep_agents requires the LiteLLM proxy client "
                    f"(LLM_PROVIDER=litellm), got {type(llm).__name__}"
                )
            mcp = loop._get_mcp_client()
            configured_model_id = llm.model_id
        except LLMConfigurationError as err:
            raise OrchestrationError(
                "llm_configuration_error", False, pending_llm_call_id, str(err)
            ) from err
        except MCPToolError as err:
            raise OrchestrationError(
                "mcp_tool_error",
                err.retryable,
                pending_llm_call_id,
                str(err),
                tool_error=_tool_error(err),
            ) from err

        try:
            agent_alias, agent_max_tokens = llm.resolve_agent_role("planner")
        except LLMProviderError as err:
            raise OrchestrationError(
                err.code, err.retryable, pending_llm_call_id, str(err)
            ) from err

        recorder = _RunRecorder(
            loop=loop,
            llm=llm,
            tracker=tracker,
            emit=emit,
            correlation=correlation,
            run_id=run_id,
            conv_id=conv_id,
            prompt=prompt,
            next_seq=next_seq,
        )
        recorder.agent_alias = agent_alias
        recorder.agent_max_tokens = agent_max_tokens
        client = http_client = httpx.Client(
            timeout=active_budgets.timeout_seconds,
            event_hooks={
                "request": [correlation.on_request],
                "response": [correlation.on_response],
            },
        )
        rec = recorder
        history = build_history_messages(loop._repo, conv_id, user_msg_id)
        initial_messages = [
            *history.messages,
            {"role": "user", "content": prompt},
        ]

        def work() -> Any:
            agent = build_deep_agent(
                client=mcp,
                base_url=llm.openai_base_url,
                api_key=llm.api_key or "unused",
                model=agent_alias,
                max_tokens=agent_max_tokens,
                http_client=client,
                timeout=active_budgets.timeout_seconds,
                before_tool_call=correlation.check_abort,
                on_compaction=lambda counts: otel_trace.get_current_span().add_event(
                    "ai.context.compacted", counts
                ),
            )
            return agent.invoke(
                {"messages": initial_messages},
                config={
                    "callbacks": [rec],
                    "recursion_limit": max(
                        active_budgets.max_iterations * RECURSION_STEPS_PER_ITERATION,
                        RECURSION_STEPS_PER_ITERATION,
                    ),
                },
            )

        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(contextvars.copy_context().run, work)
        # Close the client only once the worker is done (never mid-request); an abandoned
        # worker's in-flight request ends via the bounded per-request timeout.
        future.add_done_callback(lambda _f, c=client: c.close())
        http_client = None  # ownership handed to the callback
        while not future.done():
            if loop.is_cancelled(run_id):
                raise RunCancelledError()
            tracker.check_deadline()
            time.sleep(_POLL_INTERVAL_SECONDS)
        try:
            state = future.result()
        except _Aborted:  # pragma: no cover - defensive; main thread decides first
            raise RunCancelledError() from None
        except (BudgetExceededError, RunCancelledError, OrchestrationError):
            raise
        except LLMProviderError as err:
            raise OrchestrationError(
                err.code, err.retryable, rec.last_failed_call_id or "", str(err)
            ) from err
        except Exception as err:
            raise _map_exception(err, rec) from err

        # Answer: last AI message text (non-streaming; no answer.delta in V1).
        answer = ""
        for message in reversed(state.get("messages", [])):
            if getattr(message, "type", None) == "ai":
                answer = _message_text(message).strip()
                break
        answer = answer or _NO_ANSWER_TEXT
        report = check_answer_grounding_multi(answer, rec.evidence, question=prompt)
        grounding_report.update(
            ok=report.ok,
            ungrounded_numbers=len(report.ungrounded_numbers),
            unknown_query_ids=len(report.unknown_query_ids),
            evidence_count=len(rec.evidence),
        )
        caveat = report.caveat()
        if caveat is not None:
            rec._add_step(
                step_type="grounding_check",
                status="failed" if not report.ok else "completed",
                query_id=rec.last_query_id,
                output_summary=caveat[:200],
                metadata=report.to_metadata(),
            )
            answer = f"{answer}\n\n{caveat}"
        last_call_id = rec.llm_calls[-1].llm_call_id if rec.llm_calls else ""
        emit("answer.completed", {"answer": answer}, llm_call_id=last_call_id or None)
        loop._repo.add_message(
            Message(
                message_id=generate_message_id(),
                conversation_id=conv_id,
                sequence=rec.next_seq,
                role="assistant",
                content=answer,
            )
        )
        total_latency_ms = int((loop._monotonic() - start_mono) * 1000)
        tel = telemetry(total_latency_ms)
        finish("completed", "run.completed", tel, failure_code=None, metadata={})
        return result_for(
            "completed",
            answer,
            tel,
            sum(call.latency_ms for call in rec.llm_calls),
        )

    except RunCancelledError as err:
        abort.set()
        logger.info("Run %s cancelled by user during execution", run_id)
        latency = int((loop._monotonic() - start_mono) * 1000)
        tel = telemetry(latency)
        finish(
            "cancelled",
            "run.cancelled",
            tel,
            failure_code="cancelled",
            metadata={"reason": err.reason, "partial_text": err.partial_text},
            payload_extra={"reason": err.reason},
        )
        return result_for("cancelled", err.partial_text, tel, latency, "cancelled")
    except BudgetExceededError as err:
        abort.set()
        logger.warning("Agent loop budget exceeded: %s", err.reason)
        latency = int((loop._monotonic() - start_mono) * 1000)
        tel = telemetry(latency)
        finish(
            "budget_exceeded",
            "run.budget_exceeded",
            tel,
            failure_code="budget_exceeded",
            metadata={"reason": err.reason, "details": err.details},
            payload_extra={"reason": err.reason},
        )
        return result_for("budget_exceeded", "", tel, latency, "budget_exceeded")
    except OrchestrationError as err:
        abort.set()
        otel_trace.get_current_span().set_attribute("ai.error.category", err.code)
        latency = int((loop._monotonic() - start_mono) * 1000)
        tel = telemetry(latency)
        finish(
            "failed",
            "run.failed",
            tel,
            failure_code=err.code,
            metadata={
                "error": str(err),
                "retryable": err.retryable,
                **({"provider_error": err.details} if err.details else {}),
                **({"tool_error": err.tool_error} if err.tool_error else {}),
            },
            payload_extra={
                "retryable": err.retryable,
                "error": err.message or str(err),
                "provider_error": err.details or None,
                "tool_error": err.tool_error or None,
            },
            event_llm_call_id=err.llm_call_id or None,
        )
        raise
    finally:
        abort.set()
        if recorder is not None:
            recorder.release_open_holds()
        if http_client is not None:  # worker was never submitted
            http_client.close()
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)


def _map_exception(err: Exception, recorder: _RunRecorder) -> Exception:
    """Turn an agent failure into the manual loop's terminal exception types."""
    from langgraph.errors import GraphRecursionError

    if isinstance(err, GraphRecursionError):
        return BudgetExceededError(
            "max_iterations",
            {
                "limit": "max_iterations",
                "current": recorder.tracker.budgets.max_iterations,
            },
        )
    if _is_provider(err):
        failure = _provider_failure(err)
        return OrchestrationError(
            failure.code,
            failure.retryable,
            recorder.last_failed_call_id or "",
            str(err),
            details=failure.details,
        )
    return OrchestrationError(
        "strategy_execution_error",
        False,
        recorder.last_failed_call_id or "",
        f"{type(err).__name__}: {err}",
    )
