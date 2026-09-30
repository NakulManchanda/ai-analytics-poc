from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import BaseModel, Field

from app.scenarios.models import ScenarioConfig, ScenarioConversation, ScenarioTurn

logger = logging.getLogger(__name__)

GATEWAY_DECISION_HEADERS = (
    "x-place-decision",
    "x-placement-policy",
    "x-placement-reason",
    "x-intended-action",
    "x-admit-decision",
    "x-queue-decision",
    "x-queue-wait-ms",
    "x-overflow",
    "x-overflow-reason",
    "x-guard-decision",
    "x-policy-override-applied",
    "x-admission-mode",
)


def calculate_percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {"p50": 0.0, "p90": 0.0, "p95": 0.0, "p99": 0.0}
    sorted_vals = sorted(values)
    n = len(sorted_vals)

    def p(pct: float) -> float:
        k = (n - 1) * (pct / 100.0)
        f = int(k)
        c = f + 1
        if c < n:
            return sorted_vals[f] + (k - f) * (sorted_vals[c] - sorted_vals[f])
        return sorted_vals[f]

    return {
        "p50": round(p(50), 2),
        "p90": round(p(90), 2),
        "p95": round(p(95), 2),
        "p99": round(p(99), 2),
    }


class TurnResult(BaseModel):
    conversation_id: str
    turn_index: int
    prompt: str
    run_id: str | None = None
    status: str
    client_duration_ms: float
    server_ttft_ms: float | None = None
    # None = usage not reported (unmeasured); never estimated.
    tokens_in: int | None = None
    tokens_out: int | None = None
    error: str | None = None
    turn_metadata: dict[str, Any] = Field(default_factory=dict)
    # Per-request evidence. client_duration_ms is the end-to-end latency.
    started_at: float | None = None  # wall-clock epoch seconds
    ended_at: float | None = None
    request_id: str | None = None
    workload_class: str | None = None
    tenant_id: str | None = None
    deadline_ms: int | None = None
    # Experiment controls sent with this request (gateway_chat only).
    policy_override: str | None = None
    admission_mode: str | None = None
    # Raw gateway decision headers; None when headers are not visible (app_runs).
    gateway_headers: dict[str, str] | None = None


def _defaults(conv: ScenarioConversation, turn: ScenarioTurn) -> dict[str, Any]:
    return {
        "workload_class": turn.workload_class or conv.workload_class,
        "tenant_id": turn.tenant_id or conv.tenant_id,
    }


@dataclass
class _GatewayConv:
    """Per-conversation prompt state for gateway_chat when a system_prefix is configured."""

    prefix: str | None = None
    history: list[dict[str, str]] = field(default_factory=list)

    @property
    def prefix_tokens(self) -> int | None:
        """Estimated tokens of the system-prefix region only (gateway guard's ~4 chars/token)."""
        return (
            len(json.dumps([{"role": "system", "content": self.prefix}])) // 4
            if self.prefix
            else None
        )

    @property
    def prefix_id(self) -> str | None:
        return (
            hashlib.sha256(self.prefix.encode()).hexdigest()[:16]
            if self.prefix
            else None
        )


class ConversationResult(BaseModel):
    conversation_id: str
    turns: list[TurnResult] = Field(default_factory=list)
    success: bool = True
    total_duration_ms: float = 0.0


class ReplaySummary(BaseModel):
    scenario_name: str
    total_conversations: int
    total_turns: int
    successful_turns: int
    failed_turns: int
    duration_seconds: float
    requests_per_second: float
    latency_ms: dict[str, float]
    ttft_ms: dict[str, float]
    # None = unavailable: at least one successful turn had no reported usage.
    total_prompt_tokens: int | None = None
    total_completion_tokens: int | None = None
    tokens_measured_turns: int = 0
    tokens_unmeasured_turns: int = 0
    turn_results: list[TurnResult] = Field(default_factory=list)


class ControlNotApplied(RuntimeError):
    """The gateway did not echo a requested experiment control (ALLOW_EXPERIMENT_CONTROLS off?)."""


class ScenarioReplayer:
    def __init__(
        self,
        config: ScenarioConfig,
        target_base_url: str = "http://127.0.0.1:8080",
        client: httpx.AsyncClient | None = None,
        timeout: float = 120.0,
        use_sse: bool = True,
        gateway_stream: bool = True,
    ) -> None:
        self.config = config
        self.target_base_url = target_base_url.rstrip("/")
        self.client = client
        self.timeout = timeout
        self.use_sse = use_sse
        self.gateway_stream = gateway_stream
        self._gw_convs: dict[str, _GatewayConv] = {}
        self._control_checked = False
        self._abort: str | None = None

    async def run(self) -> ReplaySummary:
        start_time = time.perf_counter()
        semaphore = asyncio.Semaphore(self.config.concurrency)

        owns_client = self.client is None
        client = self.client or httpx.AsyncClient(timeout=self.timeout)

        try:
            tasks = [
                self._run_conversation(conv, idx, semaphore, client)
                for idx, conv in enumerate(self.config.conversations)
            ]
            conv_results = await asyncio.gather(*tasks)
        finally:
            if owns_client:
                await client.aclose()
        if self._abort:
            raise ControlNotApplied(self._abort)

        elapsed_total = max(time.perf_counter() - start_time, 0.001)

        all_turns: list[TurnResult] = []
        for cr in conv_results:
            all_turns.extend(cr.turns)

        successful_turns = [t for t in all_turns if t.status == "completed"]
        failed_turns = [t for t in all_turns if t.status != "completed"]

        latencies = [t.client_duration_ms for t in successful_turns]
        ttfts = [
            t.server_ttft_ms for t in successful_turns if t.server_ttft_ms is not None
        ]

        unmeasured = sum(
            t.tokens_in is None or t.tokens_out is None for t in successful_turns
        )
        total_prompt_tok = (
            None if unmeasured else sum(t.tokens_in or 0 for t in all_turns)
        )
        total_comp_tok = (
            None if unmeasured else sum(t.tokens_out or 0 for t in all_turns)
        )

        return ReplaySummary(
            scenario_name=self.config.name,
            total_conversations=len(self.config.conversations),
            total_turns=len(all_turns),
            successful_turns=len(successful_turns),
            failed_turns=len(failed_turns),
            duration_seconds=round(elapsed_total, 3),
            requests_per_second=round(len(all_turns) / elapsed_total, 2),
            latency_ms=calculate_percentiles(latencies),
            ttft_ms=calculate_percentiles(ttfts),
            total_prompt_tokens=total_prompt_tok,
            total_completion_tokens=total_comp_tok,
            tokens_measured_turns=len(successful_turns) - unmeasured,
            tokens_unmeasured_turns=unmeasured,
            turn_results=all_turns,
        )

    async def _run_conversation(
        self,
        conv: ScenarioConversation,
        conv_idx: int,
        semaphore: asyncio.Semaphore,
        client: httpx.AsyncClient,
    ) -> ConversationResult:
        async with semaphore:
            conv_label = f"{conv.conversation_id_prefix}_{time.time_ns()}_{conv_idx}"
            # /api/runs can create a new conversation only when conversation_id is omitted.
            # Start with an empty id (omit it in _execute_app_turn), then adopt the
            # server-returned id.
            conv_id = (
                conv_label if self.config.target_endpoint_type == "gateway_chat" else ""
            )
            if self.config.target_endpoint_type == "gateway_chat":
                self._gw_convs[conv_id] = _GatewayConv(
                    conv.system_prefix or self.config.system_prefix
                )
            conv_start = time.perf_counter()
            results: list[TurnResult] = []
            all_success = True

            for turn_idx, turn in enumerate(conv.turns):
                if turn.delay_seconds > 0:
                    await asyncio.sleep(turn.delay_seconds)

                turn_res = await self._execute_turn(
                    conv_id,
                    turn_idx,
                    turn.model_copy(update=_defaults(conv, turn)),
                    client,
                )
                results.append(turn_res)
                conv_id = turn_res.conversation_id or conv_id
                if turn_res.status != "completed":
                    all_success = False

            return ConversationResult(
                conversation_id=conv_id or conv_label,
                turns=results,
                success=all_success,
                total_duration_ms=(time.perf_counter() - conv_start) * 1000.0,
            )

    async def _execute_turn(
        self,
        conversation_id: str,
        turn_index: int,
        turn: ScenarioTurn,
        client: httpx.AsyncClient,
    ) -> TurnResult:
        started_at = time.time()
        if (
            self._abort
        ):  # first response showed a requested control was ignored: stop sending
            return TurnResult(
                conversation_id=conversation_id,
                turn_index=turn_index,
                prompt=turn.question,
                status="aborted",
                client_duration_ms=0.0,
                error=self._abort,
            )
        if self.config.target_endpoint_type == "gateway_chat":
            result = await self._execute_gateway_turn(
                conversation_id, turn_index, turn, client
            )
        else:
            result = await self._execute_app_turn(
                conversation_id, turn_index, turn, client
            )
        result.started_at = started_at
        result.ended_at = time.time()
        result.workload_class = turn.workload_class or "interactive"
        result.tenant_id = turn.tenant_id
        result.deadline_ms = turn.deadline_ms
        if self.config.target_endpoint_type == "gateway_chat":
            result.policy_override = self.config.policy_override
            result.admission_mode = self.config.admission_mode
            self._verify_controls(result)
        return result

    def _verify_controls(self, result: TurnResult) -> None:
        """A requested control must be echoed by the gateway, else the turn is not evidence."""
        seen = result.gateway_headers
        if seen is None:
            return
        cfg = self.config
        missing = [
            f"{hdr}={want!r} (got {seen.get(hdr)!r})"
            for hdr, want in (
                ("x-policy-override-applied", cfg.policy_override),
                ("x-admission-mode", cfg.admission_mode),
            )
            if want is not None and seen.get(hdr) != want
        ]
        if missing:
            msg = "requested experiment control not applied by gateway: " + ", ".join(
                missing
            )
            if not self._control_checked:
                self._abort = msg + " (is ALLOW_EXPERIMENT_CONTROLS=1 on the gateway?)"
            result.status, result.error = "control_not_applied", msg
        self._control_checked = True

    async def _execute_app_turn(
        self,
        conversation_id: str,
        turn_index: int,
        turn: ScenarioTurn,
        client: httpx.AsyncClient,
    ) -> TurnResult:
        t_start = time.perf_counter()
        url = f"{self.target_base_url}/api/runs"
        payload: dict[str, Any] = {"prompt": turn.question}
        if conversation_id:
            payload["conversation_id"] = conversation_id

        try:
            resp = await client.post(url, json=payload, timeout=self.timeout)
            if resp.status_code not in (200, 202):
                duration_ms = (time.perf_counter() - t_start) * 1000.0
                return TurnResult(
                    conversation_id=conversation_id,
                    turn_index=turn_index,
                    prompt=turn.question,
                    status=f"http_{resp.status_code}",
                    client_duration_ms=duration_ms,
                    error=f"Submit failed: {resp.status_code} - {resp.text[:200]}",
                )

            data = resp.json()
            run_id = data.get("run_id")
            actual_conv_id = data.get("conversation_id", conversation_id)

            if not run_id:
                duration_ms = (time.perf_counter() - t_start) * 1000.0
                return TurnResult(
                    conversation_id=actual_conv_id,
                    turn_index=turn_index,
                    prompt=turn.question,
                    status="invalid_response",
                    client_duration_ms=duration_ms,
                    error="No run_id returned by /api/runs",
                )

            # Follow run to terminal state
            (
                terminal_status,
                tokens_in,
                tokens_out,
                server_ttft,
                err,
            ) = await self._await_run_completion(
                run_id, actual_conv_id, client, t_start
            )
            duration_ms = (time.perf_counter() - t_start) * 1000.0

            return TurnResult(
                conversation_id=actual_conv_id,
                turn_index=turn_index,
                prompt=turn.question,
                run_id=run_id,
                status=terminal_status,
                client_duration_ms=duration_ms,
                server_ttft_ms=server_ttft,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                error=err,
            )

        except Exception as exc:
            duration_ms = (time.perf_counter() - t_start) * 1000.0
            return TurnResult(
                conversation_id=conversation_id,
                turn_index=turn_index,
                prompt=turn.question,
                status="client_exception",
                client_duration_ms=duration_ms,
                error=str(exc),
            )

    async def _await_run_completion(
        self,
        run_id: str,
        conversation_id: str,
        client: httpx.AsyncClient,
        t_start: float,
    ) -> tuple[str, int, int, float | None, str | None]:
        if self.use_sse:
            try:
                return await self._await_run_completion_sse(run_id, client, t_start)
            except Exception as sse_err:
                logger.warning(
                    "SSE stream reading failed for %s, falling back to polling: %s",
                    run_id,
                    sse_err,
                )

        return await self._await_run_completion_poll(run_id, conversation_id, client)

    async def _await_run_completion_sse(
        self, run_id: str, client: httpx.AsyncClient, t_start: float
    ) -> tuple[str, int, int, float | None, str | None]:
        events_url = f"{self.target_base_url}/api/runs/{run_id}/events"
        client_ttft: float | None = None
        server_ttft: float | None = None
        current_event_type = ""
        current_data = ""

        async with client.stream("GET", events_url, timeout=self.timeout) as stream:
            stream.raise_for_status()
            async for raw_line in stream.aiter_lines():
                line = raw_line.strip()
                if not line:
                    # Dispatch event block
                    if current_event_type and current_data:
                        try:
                            raw_data = json.loads(current_data)
                        except Exception:
                            raw_data = {}

                        # RunEvent serializes fields under 'payload' in its SSE envelope
                        if isinstance(raw_data, dict) and isinstance(
                            raw_data.get("payload"), dict
                        ):
                            payload = raw_data["payload"]
                        elif isinstance(raw_data, dict):
                            payload = raw_data
                        else:
                            payload = {}

                        event_type = current_event_type or (
                            raw_data.get("event_type")
                            if isinstance(raw_data, dict)
                            else ""
                        )

                        if client_ttft is None and (
                            event_type in ("answer.delta", "step.crewai_writer")
                            or "delta" in payload
                            or "delta" in current_data
                        ):
                            client_ttft = (time.perf_counter() - t_start) * 1000.0

                        if event_type in (
                            "run.completed",
                            "run.failed",
                            "run.budget_exceeded",
                            "run.cancelled",
                        ):
                            status = (
                                "completed"
                                if event_type == "run.completed"
                                else event_type.replace("run.", "")
                            )
                            tokens_in = payload.get("input_tokens", 0)
                            tokens_out = payload.get("output_tokens", 0)
                            telem = payload.get("telemetry", {})
                            sttft = telem.get("ttft_ms")
                            if sttft is not None:
                                try:
                                    server_ttft = float(sttft)
                                except (ValueError, TypeError):
                                    server_ttft = client_ttft
                            else:
                                server_ttft = client_ttft
                            err = payload.get("error") or payload.get("reason")
                            return status, tokens_in, tokens_out, server_ttft, err

                    current_event_type = ""
                    current_data = ""
                    continue

                if raw_line.startswith("event:"):
                    current_event_type = raw_line[len("event:") :].strip()
                elif raw_line.startswith("data:"):
                    chunk = raw_line[len("data:") :]
                    if chunk.startswith(" "):
                        chunk = chunk[1:]
                    current_data = f"{current_data}\n{chunk}" if current_data else chunk

        # If stream closed without trailing blank line, check last buffered event
        if current_data:
            try:
                raw_data = json.loads(current_data)
            except Exception:
                raw_data = {}
            payload = (
                raw_data["payload"]
                if isinstance(raw_data, dict)
                and isinstance(raw_data.get("payload"), dict)
                else (raw_data if isinstance(raw_data, dict) else {})
            )
            event_type = current_event_type or (
                raw_data.get("event_type") if isinstance(raw_data, dict) else ""
            )
            if event_type in (
                "run.completed",
                "run.failed",
                "run.budget_exceeded",
                "run.cancelled",
            ):
                status = (
                    "completed"
                    if event_type == "run.completed"
                    else event_type.replace("run.", "")
                )
                tokens_in = payload.get("input_tokens", 0)
                tokens_out = payload.get("output_tokens", 0)
                telem = payload.get("telemetry", {})
                sttft = telem.get("ttft_ms")
                try:
                    server_ttft = float(sttft) if sttft is not None else client_ttft
                except (ValueError, TypeError):
                    server_ttft = client_ttft
                err = payload.get("error") or payload.get("reason")
                return status, tokens_in, tokens_out, server_ttft, err

        # If stream closed without terminal event, fall back to polling
        raise RuntimeError("SSE stream closed without terminal event")

    async def _await_run_completion_poll(
        self, run_id: str, conversation_id: str, client: httpx.AsyncClient
    ) -> tuple[str, int, int, float | None, str | None]:
        poll_url = f"{self.target_base_url}/api/conversations/{conversation_id}"
        poll_start = time.perf_counter()

        while (time.perf_counter() - poll_start) < self.timeout:
            resp = await client.get(poll_url, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                runs = data.get("runs", [])
                matching = [r for r in runs if r.get("run_id") == run_id]
                if matching:
                    run = matching[0]
                    st = run.get("status")
                    if st in ("completed", "failed", "budget_exceeded", "cancelled"):
                        return (
                            st,
                            run.get("input_tokens", 0),
                            run.get("output_tokens", 0),
                            None,
                            (
                                None
                                if st == "completed"
                                else f"Run reached terminal state: {st}"
                            ),
                        )
            await asyncio.sleep(0.5)

        return (
            "timeout",
            0,
            0,
            None,
            f"Timed out polling run {run_id} after {self.timeout}s",
        )

    async def _execute_gateway_turn(
        self,
        conversation_id: str,
        turn_index: int,
        turn: ScenarioTurn,
        client: httpx.AsyncClient,
    ) -> TurnResult:
        t_start = time.perf_counter()
        request_id = (
            f"{self.config.name}-{conversation_id}-{turn_index}-{uuid.uuid4().hex[:8]}"
        )
        url = f"{self.target_base_url}/v1/chat/completions"
        # Without a system_prefix the bare question is sent (legacy behaviour). With one, the
        # prompt is [shared system prefix, prior turns, question] so later turns extend the
        # earlier prompt (growing conversation) and share a stable prefix across conversations.
        state = self._gw_convs.get(conversation_id) or _GatewayConv()
        user_msg = {"role": "user", "content": turn.question}
        messages = (
            [{"role": "system", "content": state.prefix}, *state.history, user_msg]
            if state.prefix
            else [user_msg]
        )
        reply: list[str] = []

        def remember() -> None:
            if state.prefix:
                state.history += [
                    user_msg,
                    {"role": "assistant", "content": "".join(reply)},
                ]

        payload = {
            "model": "Qwen/Qwen3-0.6B",
            "messages": messages,
            "max_tokens": 512,
            "temperature": 0.0,
        }
        if self.gateway_stream:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        headers = {
            "x-conversation-id": conversation_id,
            "x-agent-step": str(turn_index + 1),
            "x-request-id": request_id,
        }
        for name, value in (
            ("x-request-priority", turn.workload_class),
            ("x-tenant-id", turn.tenant_id),
            ("x-deadline-ms", turn.deadline_ms),
            ("x-prefix-id", turn.prefix_id or state.prefix_id),
            # Size of the region x-prefix-id names; only known for the default system-only id.
            ("x-prefix-tokens", None if turn.prefix_id else state.prefix_tokens),
            ("x-placement-policy-override", self.config.policy_override),
            ("x-admission-mode", self.config.admission_mode),
        ):
            if value is not None:
                headers[name] = str(value)

        rid = request_id
        seen: dict[str, str] | None = None
        try:
            async with client.stream(
                "POST", url, json=payload, headers=headers, timeout=self.timeout
            ) as resp:
                seen = {
                    h: resp.headers[h]
                    for h in GATEWAY_DECISION_HEADERS
                    if h in resp.headers
                }
                rid = resp.headers.get("x-request-id", request_id)
                is_sse = "text/event-stream" in resp.headers.get("content-type", "")
                if resp.status_code != 200 or not is_sse:
                    body = await resp.aread()
                    duration_ms = (time.perf_counter() - t_start) * 1000.0
                    if resp.status_code != 200:
                        return TurnResult(
                            conversation_id=conversation_id,
                            turn_index=turn_index,
                            prompt=turn.question,
                            status=f"http_{resp.status_code}",
                            client_duration_ms=duration_ms,
                            gateway_headers=seen,
                            request_id=rid,
                            error=f"Gateway error: {resp.status_code} - "
                            f"{body.decode(errors='replace')[:200]}",
                        )
                    data = json.loads(body)
                    usage = data.get("usage") or {}
                    for choice in data.get("choices") or []:
                        reply.append((choice.get("message") or {}).get("content") or "")
                    remember()
                    return TurnResult(
                        conversation_id=conversation_id,
                        turn_index=turn_index,
                        prompt=turn.question,
                        run_id=data.get("id"),
                        status="completed",
                        client_duration_ms=duration_ms,
                        tokens_in=usage.get("prompt_tokens"),
                        tokens_out=usage.get("completion_tokens"),
                        gateway_headers=seen,
                        request_id=rid,
                    )

                ttft: float | None = None
                done = False
                usage: dict[str, Any] = {}
                run_id: str | None = None
                error: str | None = None
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[len("data:") :].strip()
                    if raw == "[DONE]":
                        done = True
                        break
                    try:
                        chunk = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(chunk, dict):
                        continue
                    if chunk.get("error"):
                        error = str(chunk["error"])[:200]
                        break
                    run_id = run_id or chunk.get("id")
                    usage = chunk.get("usage") or usage
                    for choice in chunk.get("choices") or []:
                        if piece := (choice.get("delta") or {}).get("content"):
                            reply.append(piece)
                            if ttft is None:
                                ttft = (time.perf_counter() - t_start) * 1000.0
                duration_ms = (time.perf_counter() - t_start) * 1000.0
                if done and not error:
                    remember()
                return TurnResult(
                    conversation_id=conversation_id,
                    turn_index=turn_index,
                    prompt=turn.question,
                    run_id=run_id,
                    status=(
                        "stream_error"
                        if error
                        else "completed" if done else "incomplete_stream"
                    ),
                    client_duration_ms=duration_ms,
                    server_ttft_ms=ttft,
                    tokens_in=usage.get("prompt_tokens"),
                    tokens_out=usage.get("completion_tokens"),
                    gateway_headers=seen,
                    request_id=rid,
                    error=error or (None if done else "Stream ended without [DONE]"),
                )
        except Exception as exc:
            duration_ms = (time.perf_counter() - t_start) * 1000.0
            return TurnResult(
                conversation_id=conversation_id,
                turn_index=turn_index,
                prompt=turn.question,
                status="client_exception",
                client_duration_ms=duration_ms,
                request_id=rid,
                gateway_headers=seen,
                error=str(exc),
            )
