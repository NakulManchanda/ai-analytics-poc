"""Inference Gateway: guard -> tenant quota -> admit -> place -> proxy (#121, #122 slices 1-2)."""

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

try:  # package import (tests) or flat import (ConfigMap mounted at /app, `uvicorn main:app`)
    from . import guard as guard_mod
    from . import metrics, placement
    from .admission import AdmitConfig, AdmitRequest, Shed, should_shed
    from .tenants import TenantQuota
    from .workers import build_registry
except ImportError:
    import guard as guard_mod
    import placement
    from admission import AdmitConfig, AdmitRequest, Shed, should_shed
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
) -> Response:
    try:
        body = await request.json()
    except ValueError:
        body = None
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
        result = quota.acquire(x_tenant_id, est_tokens, now)
        if not isinstance(result, Shed):
            lease = result
            result = should_shed(
                AdmitRequest(est_tokens, _int_or_none(x_deadline_ms), klass),
                list(registry.snapshots.values()),
                now=now,
                cfg=ADMIT_CFG,
            )
    if isinstance(result, Shed):
        metrics.ADMIT.labels("shed", result.reason, klass).inc()
        metrics.SHED.labels(result.reason, klass, str(result.code)).inc()
        metrics.TENANT_REQUESTS.labels(tenant, "shed").inc()
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
                "admission_inputs": result.inputs,
            }
        )
    )

    with metrics.timed("place", klass):
        decision = placement.pick(
            placement.PlacementRequest(x_prefix_id, est_tokens, klass, x_force_worker),
            list(registry.snapshots.values()),
            policy=PLACEMENT_POLICY,
            stale_after=SNAPSHOT_STALE_S,
            rr_index=registry.next_rr(),
            allow_forced=os.getenv("ALLOW_FORCED_PLACEMENT") == "1",
        )
    if isinstance(decision, placement.PlacementError):
        metrics.PLACEMENT_ERRORS.labels(decision.reason).inc()
        return reject(503, decision.reason, "no worker available for placement", "place")

    metrics.PICKS.labels(
        decision.placement_policy, decision.chosen_worker, decision.placement_reason
    ).inc()
    if decision.fallback:
        metrics.STALE_FALLBACK.inc()
    snap = registry.snapshots[decision.chosen_worker]
    registry.record_prefix(decision.chosen_worker, x_prefix_id, est_tokens)
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

    target_url = f"{snap.worker.url}/v1/chat/completions"
    snap.inflight += 1
    snap.inflight_tokens += est_tokens

    proxy_start = time.perf_counter()

    def release(status: int) -> None:
        lease.release(refund=status >= 500)  # worker failure: tokens weren't served
        metrics.STAGE_DURATION.labels("proxy", klass).observe(time.perf_counter() - proxy_start)
        snap.inflight -= 1
        snap.inflight_tokens -= est_tokens
        metrics.REQUESTS.labels(str(status), klass).inc()

    client = httpx.AsyncClient(timeout=60.0)

    if bool(body.get("stream", False)):

        async def stream_generator():
            status = 200
            try:
                async with client.stream(
                    "POST",
                    target_url,
                    json=body,
                    headers={"content-type": "application/json"},
                ) as upstream_resp:
                    status = upstream_resp.status_code
                    if status != 200:
                        err_content = await upstream_resp.aread()
                        decoded_err = err_content.decode("utf-8", errors="replace")
                        yield f"data: {json.dumps({'error': decoded_err})}\n\n"
                        return
                    async for line in upstream_resp.aiter_lines():
                        if line:
                            yield f"{line}\n\n"
            except httpx.RequestError as exc:
                status = 502
                yield f"data: {json.dumps({'error': str(exc)})}\n\n"
            finally:
                release(status)
                await client.aclose()

        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream",
            headers=correlation_headers,
        )

    try:
        upstream_resp = await client.post(
            target_url,
            json=body,
            headers={"content-type": "application/json"},
        )
    except httpx.ConnectError as exc:
        release(503)
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

    release(upstream_resp.status_code)
    content = (
        upstream_resp.json() if upstream_resp.status_code == 200 else {"error": upstream_resp.text}
    )
    return JSONResponse(
        status_code=upstream_resp.status_code,
        content=content,
        headers=correlation_headers,
    )
