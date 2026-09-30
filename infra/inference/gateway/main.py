"""Inference Gateway: guard -> quota -> admit -> place -> queue -> proxy (#121, #122 slices 1-4)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

try:  # package import (tests) or flat import (ConfigMap mounted at /app, `uvicorn main:app`)
    from . import guard as guard_mod
    from . import metrics, placement
    from .admission import AdmitConfig, AdmitRequest, Shed, should_shed
    from .overflow import Overflow, OverflowConfig, decide
    from .queueing import CLASSES, QueueConfig, QueueRejected, WorkerQueues
    from .tenants import TenantQuota
    from .workers import build_registry
except ImportError:
    import guard as guard_mod
    import placement
    from admission import AdmitConfig, AdmitRequest, Shed, should_shed
    from overflow import Overflow, OverflowConfig, decide
    from queueing import CLASSES, QueueConfig, QueueRejected, WorkerQueues
    from tenants import TenantQuota
    from workers import build_registry

    import metrics

log = logging.getLogger("inference.gateway")
logging.basicConfig(level=logging.INFO)

PLACEMENT_POLICY = os.getenv("PLACEMENT_POLICY", "prefix_then_load")
SNAPSHOT_REFRESH_S = float(os.getenv("SNAPSHOT_REFRESH_S", "1.0"))
SNAPSHOT_STALE_S = float(os.getenv("SNAPSHOT_STALE_S", "5.0"))

ADMIT_CFG = AdmitConfig.from_env()
quota = TenantQuota.from_env()
registry = build_registry()
queues = WorkerQueues(QueueConfig.from_env())
OVERFLOW_CFG = OverflowConfig.from_env()
DEFAULT_WORKER_URL = registry.snapshots["worker_a"].worker.url  # /tokenize goes to A


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    async def refresh_loop() -> None:
        async with httpx.AsyncClient(timeout=2.0) as client:
            while True:
                await registry.refresh(client)
                await asyncio.sleep(SNAPSHOT_REFRESH_S)

    # One awaited scrape before serving so the first request does not see no_signal.
    async with httpx.AsyncClient(timeout=2.0) as client:
        await registry.refresh(client)
    task = asyncio.create_task(refresh_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(
    title="Inference Gateway",
    description=(
        "Guard and place model calls across vLLM workers while instrumenting correlation metadata."
    ),
    version="0.2.0",
    lifespan=lifespan,
)


@app.get("/metrics")
async def prometheus_metrics() -> Response:
    metrics.observe_snapshots(registry.snapshots.values(), SNAPSHOT_STALE_S)
    metrics.observe_queues(queues, registry.snapshots, CLASSES)
    body, content_type = metrics.render()
    return Response(body, media_type=content_type)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "inference-gateway"}


@app.api_route("/tokenize", methods=["POST"])
async def tokenize(request: Request) -> Response:
    """Minimal pass-through to the vLLM worker's OpenAI-compatible /tokenize
    endpoint (D17): exact token region counts for the rendered prefix, with
    no local tokenizer dependency in the app."""
    body = await request.json()
    target_url = f"{DEFAULT_WORKER_URL}/tokenize"
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            upstream_resp = await client.post(
                target_url,
                json=body,
                headers={"content-type": "application/json"},
            )
        except httpx.ConnectError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Worker unavailable at {DEFAULT_WORKER_URL}: {exc}",
            ) from exc
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Error proxying to worker: {exc}",
            ) from exc
    content = (
        upstream_resp.json() if upstream_resp.status_code == 200 else {"error": upstream_resp.text}
    )
    return JSONResponse(status_code=upstream_resp.status_code, content=content)


class _OverflowStatus(ValueError):
    def __init__(self, status: int) -> None:
        super().__init__(f"overflow status {status}")
        self.status = status


def _fallback_failure(exc: Exception) -> str:
    """Bounded classification of an overflow failure (label for overflow_error_total)."""
    if isinstance(exc, httpx.TimeoutException):
        return "fallback_timeout"
    if isinstance(exc, _OverflowStatus) and exc.status >= 500:
        return "fallback_5xx"
    return "fallback_error"


async def _overflow(
    cfg: OverflowConfig,
    reason: str,
    body: dict,
    headers: dict,
    log_fields: dict,
    on_done,
    remaining_s: float | None = None,
    on_first=lambda: None,
) -> Response | None:
    """ONE attempt at the configured destination (model rewritten). None -> caller returns
    the original local error. ``on_done(status)`` runs once when the client-visible response
    is complete (after the stream ends for streaming). ``remaining_s`` is the request's
    remaining x-deadline-ms budget. ``on_first()`` marks the first generated token (TTFT).
    Never logs the API key or exception text."""
    dest = {"overflow_provider": cfg.provider, "overflow_model": cfg.model}
    if remaining_s is not None and remaining_s <= 0:
        metrics.OVERFLOW_ERROR.labels("no_time_remaining").inc()
        log.info(
            json.dumps(
                {**log_fields, "stage": "overflow", "reason": reason, **dest}
                | {"outcome": "skipped", "skip": "no_time_remaining"}
            )
        )
        return None
    out_headers = {
        **headers,
        "x-orchestration-stage": "overflow",
        "x-place-decision": "overflow",
        "x-overflow": cfg.destination,
        "x-overflow-reason": reason,
    }
    payload = {**body, "model": cfg.model}
    timeout = cfg.timeout_s if remaining_s is None else min(cfg.timeout_s, remaining_s)
    client = httpx.AsyncClient(timeout=timeout)
    cm = None

    def record(outcome: str, exc: Exception | None = None) -> None:
        metrics.OVERFLOW.labels(reason, cfg.provider, cfg.model, outcome).inc()
        fields = {**log_fields, "stage": "overflow", "reason": reason, **dest, "outcome": outcome}
        if exc is not None:
            metrics.OVERFLOW_ERROR.labels(_fallback_failure(exc)).inc()
            fields["error_type"] = type(exc).__name__
        log.info(json.dumps(fields))

    try:
        if body.get("stream"):
            cm = client.stream("POST", cfg.url, json=payload, headers=cfg.headers())
            upstream = await cm.__aenter__()
            if upstream.status_code != 200:
                raise _OverflowStatus(upstream.status_code)
        else:
            upstream = await client.post(cfg.url, json=payload, headers=cfg.headers())
            if upstream.status_code != 200:
                raise _OverflowStatus(upstream.status_code)
            content = upstream.json()
    except (httpx.HTTPError, ValueError) as exc:
        if cm is not None:
            with contextlib.suppress(Exception):
                await cm.__aexit__(None, None, None)
        await client.aclose()
        record("error", exc)
        return None
    if cm is None:
        await client.aclose()
        record("ok")
        on_done(200)
        return JSONResponse(content=content, headers=out_headers)

    async def gen():
        status = 499  # client went away unless the stream completes or fails below
        try:
            async for line in upstream.aiter_lines():
                if line:
                    if _has_content(line):
                        on_first()
                    yield f"{line}\n\n"
            status = 200
            record("ok")
        except httpx.HTTPError as exc:
            status = 502
            record("error", exc)
            yield f"data: {json.dumps({'error': 'overflow_stream_error'})}\n\n"
        finally:
            with contextlib.suppress(Exception):
                await cm.__aexit__(None, None, None)
            await client.aclose()
            on_done(status)

    async def cleanup() -> None:  # safety net if the generator never started
        on_done(499)
        await client.aclose()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers=out_headers,
        background=BackgroundTask(cleanup),
    )


def _has_content(line: str) -> bool:
    """True for an SSE ``data:`` record carrying a non-empty generated token (chat delta content
    or completions text). Role-only deltas, keepalives, errors, [DONE], and non-JSON are not."""
    if not line.startswith("data:"):
        return False
    try:
        choice = json.loads(line[5:])["choices"][0]
        return bool(choice.get("text") or (choice.get("delta") or {}).get("content"))
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        return False


def _ms(seconds: float) -> int:
    return round(seconds * 1000)


class _NoLease:  # x-tenant-quota-mode: off (experiments only): nothing acquired, nothing to free
    def release(self, refund: bool = False) -> None:
        return None


def _experiment_controls(policy: str | None, admission: str | None, quota_mode: str | None):
    """Test-only overrides (#123): honored only with ALLOW_EXPERIMENT_CONTROLS=1, else ignored
    entirely (production traffic can never steer placement or skip admission). Returns
    (policy, admission_off, quota_off) or None when a value is invalid (caller answers 400)."""
    if os.getenv("ALLOW_EXPERIMENT_CONTROLS") != "1":
        return None, False, False
    if (
        (policy is not None and policy not in placement.POLICIES)
        or (admission is not None and admission not in ("on", "off"))
        or (quota_mode is not None and quota_mode not in ("on", "off"))
    ):
        return None
    return policy, admission == "off", quota_mode == "off"


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value) if value is not None and int(value) >= 0 else None
    except ValueError:
        return None


@app.api_route("/serve", methods=["POST"])
@app.api_route("/v1/chat/completions", methods=["POST"])
async def serve_completion(
    request: Request,
    x_request_id: str | None = Header(None),
    x_conversation_id: str | None = Header(None),
    x_agent_step: str | None = Header(None),
    x_prefix_id: str | None = Header(None),
    x_tenant_id: str | None = Header(None),
    x_request_priority: str | None = Header(None),
    x_estimated_prompt_tokens: str | None = Header(None),
    x_deadline_ms: str | None = Header(None),
    x_force_worker: str | None = Header(None),
    x_prefix_tokens: str | None = Header(None),
    x_placement_policy_override: str | None = Header(None),
    x_admission_mode: str | None = Header(None),
    x_tenant_quota_mode: str | None = Header(None),
) -> Response:
    try:
        body = await request.json()
    except ValueError:
        body = None
    started = time.monotonic()
    deadline_ms = _int_or_none(x_deadline_ms)
    klass = "batch" if x_request_priority == "batch" else "interactive"
    correlation_headers = {
        "x-request-id": x_request_id or "req-untracked",
        "x-conversation-id": x_conversation_id or "conv-untracked",
        "x-agent-step": x_agent_step or "0",
        "x-prefix-id": x_prefix_id or "prefix-none",
        "x-orchestration-stage": "guard",
        "x-guard-decision": "allow",
        "x-admit-decision": "accept",
        "x-place-decision": "none",
    }
    log_fields = {
        "request_id": correlation_headers["x-request-id"],
        "conversation_id": correlation_headers["x-conversation-id"],
        "prefix_id": x_prefix_id,
        "tenant_id": x_tenant_id,
        "workload_class": klass,
    }

    lease = None
    tenant = quota.bucket_name(x_tenant_id)
    ttft_seen = False

    def observe_ttft() -> None:  # once per streaming request: first content chunk
        nonlocal ttft_seen
        if not ttft_seen:
            ttft_seen = True
            metrics.TTFT.labels(klass).observe(time.monotonic() - started)

    controls = _experiment_controls(
        x_placement_policy_override, x_admission_mode, x_tenant_quota_mode
    )
    invalid_controls = controls is None
    policy_override, admission_off, quota_off = controls or (None, False, False)
    if policy_override:
        correlation_headers["x-policy-override-applied"] = policy_override
        log_fields["policy_override"] = policy_override
    if os.getenv("ALLOW_EXPERIMENT_CONTROLS") == "1" and x_admission_mode in ("on", "off"):
        correlation_headers["x-admission-mode"] = x_admission_mode
        log_fields["admission_mode"] = x_admission_mode
    if quota_off:
        log_fields["tenant_quota_mode"] = "off"

    async def try_overflow(
        code: int, reason: str, source: str, never_overflow: bool = False
    ) -> Response | None:
        """Overflow a local 503/529 if eligible and configured; the request never bounces
        back to local. On success the tenant tokens stay charged and the concurrency lease is
        held until the overflow response completes; on None the caller does its normal
        accounting (refund + final status)."""
        if not OVERFLOW_CFG.active or not isinstance(
            decide(code, reason, never_overflow, source), Overflow
        ):
            return None
        remaining = None if deadline_ms is None else started + deadline_ms / 1000 - time.monotonic()

        done = False

        def once(status: int) -> None:
            nonlocal done
            if done:
                return
            done = True
            if lease is not None:
                lease.release()  # overflow consumed tokens: no refund
            metrics.REQUESTS.labels(str(status), klass).inc()

        return await _overflow(
            OVERFLOW_CFG,
            reason,
            body,
            correlation_headers,
            log_fields,
            once,
            remaining,
            observe_ttft,
        )

    def reject(
        status: int,
        code: str,
        reason: str,
        stage: str,
        extra: dict | None = None,
        **hdrs: str,
    ) -> JSONResponse:
        if lease is not None:
            lease.release(refund=True)  # never dispatched: no tokens consumed
        metrics.REQUESTS.labels(str(status), klass).inc()
        log.info(
            json.dumps(
                {
                    **log_fields,
                    "stage": stage,
                    "code": code,
                    "reason": reason,
                    **(extra or {}),
                }
            )
        )
        headers = {**correlation_headers, "x-orchestration-stage": stage, **hdrs}
        return JSONResponse(
            status_code=status,
            content={"error": code, "detail": reason},
            headers=headers,
        )

    if invalid_controls:  # bounded reason: never echo the header values back
        return reject(
            400,
            "invalid_experiment_control",
            "unsupported experiment control header value",
            "guard",
        )

    with metrics.timed("guard", klass):
        verdict = guard_mod.inspect(body, estimated_tokens_header=x_estimated_prompt_tokens)
    if not verdict.ok:
        metrics.GUARD_REJECT.labels(verdict.code).inc()
        return reject(
            verdict.http_status,
            verdict.code,
            verdict.reason,
            "guard",
            **{
                "x-guard-decision": f"reject:{verdict.code}",
                "x-admit-decision": "not_evaluated",
            },
        )

    est_tokens = guard_mod.estimate_prompt_tokens(body, x_estimated_prompt_tokens)
    now = time.monotonic()
    with metrics.timed("admit", klass):
        # Experiment controls skip only what they name; guard always ran, quota stays on by default.
        result = None if quota_off else quota.acquire(x_tenant_id, est_tokens, now)
        if not isinstance(result, Shed):
            lease = _NoLease() if quota_off else result
            result = (
                None
                if admission_off
                else should_shed(
                    AdmitRequest(est_tokens, _int_or_none(x_deadline_ms), klass),
                    list(registry.snapshots.values()),
                    now=now,
                    cfg=ADMIT_CFG,
                )
            )
    if isinstance(result, Shed):
        metrics.ADMIT.labels("shed", result.reason, klass).inc()
        metrics.SHED.labels(result.reason, klass, str(result.code)).inc()
        metrics.TENANT_REQUESTS.labels(tenant, "shed").inc()
        if resp := await try_overflow(result.code, result.reason, "admit", result.never_overflow):
            return resp
        return reject(
            result.code,
            result.reason,
            "request shed by admission",
            "admit",
            {
                "admission_inputs": result.inputs,
                "never_overflow": result.never_overflow,
            },
            **{
                "x-admit-decision": f"shed:{result.reason}",
                "retry-after": str(result.retry_after_seconds),
            },
        )
    metrics.ADMIT.labels("accept", "ok", klass).inc()
    metrics.TENANT_REQUESTS.labels(tenant, "admitted").inc()
    log.info(
        json.dumps(
            {
                **log_fields,
                "stage": "admit",
                "decision": "accept",
                "admission_inputs": result.inputs if result else {"skipped": "admission_off"},
            }
        )
    )

    for w in registry.snapshots.values():
        w.queued = queues.depth(w.id)  # gateway queue depth participates in the load score
    # x-prefix-tokens sizes the region x-prefix-id names (e.g. system prefix only). Absent or
    # invalid = legacy: the whole prompt is believed reusable, which over-counts prompts that
    # carry a per-turn suffix or divergent history.
    prefix_tokens = _int_or_none(x_prefix_tokens)
    if prefix_tokens is not None:
        prefix_tokens = min(prefix_tokens, est_tokens)
    with metrics.timed("place", klass):
        decision = placement.pick(
            placement.PlacementRequest(x_prefix_id, est_tokens, klass, x_force_worker),
            list(registry.snapshots.values()),
            policy=policy_override or PLACEMENT_POLICY,
            stale_after=SNAPSHOT_STALE_S,
            rr_index=registry.next_rr(),
            allow_forced=os.getenv("ALLOW_FORCED_PLACEMENT") == "1",
        )
    if isinstance(decision, placement.PlacementError):
        metrics.PLACEMENT_ERRORS.labels(decision.reason).inc()
        if resp := await try_overflow(503, decision.reason, "place"):
            return resp
        return reject(503, decision.reason, "no worker available for placement", "place")

    metrics.PICKS.labels(
        decision.placement_policy, decision.chosen_worker, decision.placement_reason
    ).inc()
    if decision.fallback:
        metrics.STALE_FALLBACK.inc()
    snap = registry.snapshots[decision.chosen_worker]
    registry.record_prefix(
        decision.chosen_worker, x_prefix_id, est_tokens if prefix_tokens is None else prefix_tokens
    )
    correlation_headers.update(
        {
            "x-orchestration-stage": "worker_dispatch",
            "x-place-decision": decision.chosen_worker,
            "x-placement-policy": decision.placement_policy,
            "x-placement-reason": decision.placement_reason,
            "x-intended-action": decision.intended_action,
        }
    )
    log.info(json.dumps({**log_fields, "stage": "place", **vars(decision)}))

    deadline_at = now + deadline_ms / 1000 if deadline_ms is not None else None
    queue_enter = time.time()
    try:
        with metrics.timed("queue", klass):
            ticket = await queues.acquire(snap.id, klass, est_tokens, deadline_at)
    except QueueRejected as exc:
        metrics.QUEUE_ERRORS.labels(exc.reason, klass).inc()
        if resp := await try_overflow(503, exc.reason, "queue"):
            return resp
        return reject(
            503,
            exc.reason,
            "request could not get a dispatch slot",
            "queue",
            {"queue_enter": queue_enter, "queue_wait_ms": _ms(time.time() - queue_enter)},
            **{
                "x-queue-decision": exc.reason,
                "x-queue-wait-ms": str(_ms(time.time() - queue_enter)),
                "retry-after": str(ADMIT_CFG.retry_after_s),
            },
        )
    except asyncio.CancelledError:  # client went away while queued: nothing dispatched
        lease.release(refund=True)
        raise
    queue_wait_ms = _ms(ticket.wait_s)
    metrics.QUEUE_WAIT.labels(snap.id, klass).observe(ticket.wait_s)
    correlation_headers.update(
        {"x-queue-decision": "dispatched", "x-queue-wait-ms": str(queue_wait_ms)}
    )
    log.info(
        json.dumps(
            {
                **log_fields,
                "stage": "queue",
                "queue_enter": queue_enter,
                "queue_dispatch": queue_enter + ticket.wait_s,
                "queue_wait_ms": queue_wait_ms,
            }
        )
    )

    target_url = f"{snap.worker.url}/v1/chat/completions"
    snap.inflight += 1
    snap.inflight_tokens += est_tokens

    proxy_start = time.perf_counter()

    freed = finished = False

    def free_worker() -> None:  # worker-side resources; idempotent
        nonlocal freed
        if freed:
            return
        freed = True
        metrics.STAGE_DURATION.labels("proxy", klass).observe(time.perf_counter() - proxy_start)
        snap.inflight -= 1
        snap.inflight_tokens -= est_tokens
        ticket.release()
        log.info(json.dumps({**log_fields, "stage": "queue", "queue_release": time.time()}))

    def finish(status: int) -> None:  # final client status + tenant lease; idempotent
        nonlocal finished
        if finished:
            return
        finished = True
        lease.release(refund=status >= 500)  # worker failure: tokens weren't served
        metrics.REQUESTS.labels(str(status), klass).inc()

    def release(status: int) -> None:
        free_worker()
        finish(status)

    client = httpx.AsyncClient(timeout=60.0)

    async def stream_cleanup() -> None:
        # Safety net if the client disconnects before the generator ever starts.
        release(499)
        await client.aclose()

    if bool(body.get("stream", False)):
        cm = client.stream(
            "POST", target_url, json=body, headers={"content-type": "application/json"}
        )
        try:  # open before returning so a local 503/529 is known before any bytes stream
            upstream_resp = await cm.__aenter__()
        except httpx.RequestError as exc:
            code = 503 if isinstance(exc, httpx.ConnectError) else 502
            free_worker()
            await client.aclose()
            if resp := await try_overflow(code, "worker_unavailable", "upstream"):
                return resp
            finish(code)
            raise HTTPException(
                status_code=code,
                detail=f"Worker unavailable at {snap.worker.url}: {exc}",
                headers=correlation_headers,
            ) from exc
        if upstream_resp.status_code in (503, 529):
            err = (await upstream_resp.aread()).decode("utf-8", errors="replace")
            free_worker()
            await cm.__aexit__(None, None, None)
            await client.aclose()
            if resp := await try_overflow(
                upstream_resp.status_code, "worker_overloaded", "upstream"
            ):
                return resp
            finish(upstream_resp.status_code)
            return JSONResponse(
                status_code=upstream_resp.status_code,
                content={"error": err},
                headers=correlation_headers,
            )

        async def stream_generator():
            status = upstream_resp.status_code
            try:
                if status != 200:
                    err_content = await upstream_resp.aread()
                    decoded_err = err_content.decode("utf-8", errors="replace")
                    yield f"data: {json.dumps({'error': decoded_err})}\n\n"
                    return
                async for line in upstream_resp.aiter_lines():
                    if line:
                        if _has_content(line):
                            observe_ttft()
                        yield f"{line}\n\n"
            except httpx.RequestError as exc:
                status = 502
                yield f"data: {json.dumps({'error': str(exc)})}\n\n"
            finally:
                release(status)
                await cm.__aexit__(None, None, None)
                await client.aclose()

        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream",
            headers=correlation_headers,
            background=BackgroundTask(stream_cleanup),
        )

    try:
        upstream_resp = await client.post(
            target_url,
            json=body,
            headers={"content-type": "application/json"},
        )
    except httpx.ConnectError as exc:
        free_worker()
        if resp := await try_overflow(503, "worker_unavailable", "upstream"):
            return resp
        finish(503)
        raise HTTPException(
            status_code=503,
            detail=f"Worker unavailable at {snap.worker.url}: {exc}",
            headers=correlation_headers,
        ) from exc
    except httpx.RequestError as exc:
        release(502)
        raise HTTPException(
            status_code=502,
            detail=f"Error proxying to worker: {exc}",
            headers=correlation_headers,
        ) from exc
    finally:
        await client.aclose()

    free_worker()
    if upstream_resp.status_code in (503, 529) and (
        resp := await try_overflow(upstream_resp.status_code, "worker_overloaded", "upstream")
    ):
        return resp
    finish(upstream_resp.status_code)
    content = (
        upstream_resp.json() if upstream_resp.status_code == 200 else {"error": upstream_resp.text}
    )
    return JSONResponse(
        status_code=upstream_resp.status_code,
        content=content,
        headers=correlation_headers,
    )
