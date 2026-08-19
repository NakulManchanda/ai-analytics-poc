from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.events.models import RunEvent
from app.events.publisher import RUN_EVENTS_STREAM
from app.state import InMemoryStateRepository, StateRepository

logger = logging.getLogger(__name__)
HEARTBEAT_INTERVAL_SECONDS = 15.0
POLL_INTERVAL_SECONDS = 0.5
MAX_STREAM_WAIT_SECONDS = 30.0


def create_events_router(
    redis_url: str | None = None,
    state_repository: StateRepository | None = None,
    redis_client: Any = None,
) -> APIRouter:
    router = APIRouter(prefix="/api")
    repo = state_repository or InMemoryStateRepository()
    resolved_redis_url = redis_url or os.environ.get(
        "REDIS_URL", "redis://localhost:6379/0"
    )

    def get_redis_client() -> Any:
        if redis_client is not None:
            return redis_client
        import redis

        return redis.Redis.from_url(resolved_redis_url, decode_responses=True)

    @router.get("/runs/{run_id}/events")
    async def stream_run_events(
        run_id: str,
        request: Request,
    ) -> StreamingResponse:
        # 1. Verify run exists in durable state repository
        durable_run = repo.get_run(run_id)
        if durable_run is None:
            raise HTTPException(status_code=404, detail="Run not found")

        last_event_id = request.headers.get("last-event-id") or "0-0"

        async def event_generator() -> AsyncGenerator[str, None]:
            emitted_event_ids: set[str] = set()
            start_time = asyncio.get_event_loop().time()
            last_stream_id = last_event_id if "-" in last_event_id else "0-0"

            # Try reading from Redis Stream
            try:
                client = get_redis_client()
            except Exception:
                client = None

            terminal_seen = False
            last_heartbeat = asyncio.get_event_loop().time()

            while not terminal_seen:
                if await request.is_disconnected():
                    logger.info(
                        "Client disconnected from SSE stream for run %s", run_id
                    )
                    break

                events_found = False
                if client is not None:
                    try:
                        # Read new events from Redis Stream
                        streams = client.xread(
                            {RUN_EVENTS_STREAM: last_stream_id}, count=50, block=500
                        )
                        if streams:
                            for _stream_name, messages in streams:
                                for msg_id, raw_data in messages:
                                    last_stream_id = msg_id
                                    if raw_data.get("run_id") == run_id:
                                        evt = RunEvent.from_dict(raw_data)
                                        if evt.event_id not in emitted_event_ids:
                                            emitted_event_ids.add(evt.event_id)
                                            events_found = True
                                            yield evt.to_sse()
                                            if evt.event_type in (
                                                "run.completed",
                                                "run.failed",
                                                "run.budget_exceeded",
                                            ):
                                                terminal_seen = True
                                                break
                    except Exception as error:
                        logger.warning(
                            "Redis stream read error for run %s: %s", run_id, error
                        )
                        client = None

                # Fallback / Reconciliation from Durable State Repository
                if not events_found and (client is None or not emitted_event_ids):
                    current_run = repo.get_run(run_id)
                    if current_run is not None and current_run.status in (
                        "completed",
                        "budget_exceeded",
                        "failed",
                    ):
                        steps = repo.list_run_steps(run_id)
                        seq = 1
                        # Reconstruct received
                        init_evt = RunEvent(
                            event_type="run.received",
                            run_id=run_id,
                            conversation_id=current_run.conversation_id,
                            sequence=seq,
                            payload={"status": "in_progress"},
                        )
                        if init_evt.event_id not in emitted_event_ids:
                            emitted_event_ids.add(init_evt.event_id)
                            yield init_evt.to_sse()

                        for step in steps:
                            seq += 1
                            step_evt = RunEvent(
                                event_type=f"step.{step.step_type}",
                                run_id=run_id,
                                conversation_id=current_run.conversation_id,
                                sequence=seq,
                                step_id=step.step_id,
                                llm_call_id=step.llm_call_id,
                                tool_call_id=step.tool_call_id,
                                query_id=step.query_id,
                                payload={
                                    "status": step.status,
                                    "input_summary": step.input_summary,
                                    "output_summary": step.output_summary,
                                },
                            )
                            if step_evt.event_id not in emitted_event_ids:
                                emitted_event_ids.add(step_evt.event_id)
                                yield step_evt.to_sse()

                        seq += 1
                        term_type = (
                            "run.completed"
                            if current_run.status == "completed"
                            else f"run.{current_run.status}"
                        )
                        term_evt = RunEvent(
                            event_type=term_type,
                            run_id=run_id,
                            conversation_id=current_run.conversation_id,
                            sequence=seq,
                            payload={
                                "status": current_run.status,
                                "input_tokens": current_run.input_tokens,
                                "output_tokens": current_run.output_tokens,
                                "estimated_cost_usd": current_run.estimated_cost_usd,
                                "failure_code": current_run.failure_code,
                            },
                        )
                        if term_evt.event_id not in emitted_event_ids:
                            emitted_event_ids.add(term_evt.event_id)
                            yield term_evt.to_sse()
                        terminal_seen = True
                        break

                now = asyncio.get_event_loop().time()
                # Send periodic heartbeat comment
                if now - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                    yield ": keep-alive\n\n"
                    last_heartbeat = now

                if now - start_time > MAX_STREAM_WAIT_SECONDS:
                    logger.info(
                        "SSE stream reached max wait timeout for run %s", run_id
                    )
                    break

                if not events_found:
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return router
