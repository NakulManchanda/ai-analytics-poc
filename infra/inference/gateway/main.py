"""Thin Inference Gateway service for Issue #121."""

from __future__ import annotations

import json
import os

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

DEFAULT_WORKER_URL = os.getenv(
    "DEFAULT_WORKER_URL",
    "http://inference-worker-a.inference-lab.svc.cluster.local:8000",
).rstrip("/")

app = FastAPI(
    title="Inference Gateway",
    description=(
        "Thin serve path proxying model calls to vLLM workers "
        "while instrumenting correlation metadata."
    ),
    version="0.1.0",
)



@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "inference-gateway"}


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
) -> Response:
    body = await request.json()
    is_stream = bool(body.get("stream", False))

    # Instrument pass-through response headers
    correlation_headers = {
        "x-request-id": x_request_id or "req-untracked",
        "x-conversation-id": x_conversation_id or "conv-untracked",
        "x-agent-step": x_agent_step or "0",
        "x-prefix-id": x_prefix_id or "prefix-none",
        "x-orchestration-stage": "worker_passthrough",
        "x-guard-decision": "allow",
        "x-admit-decision": "accept",
        "x-place-decision": "worker_a",
    }

    target_url = f"{DEFAULT_WORKER_URL}/v1/chat/completions"

    client = httpx.AsyncClient(timeout=60.0)

    if is_stream:
        async def stream_generator():
            try:
                async with client.stream(
                    "POST",
                    target_url,
                    json=body,
                    headers={"content-type": "application/json"},
                ) as upstream_resp:
                    if upstream_resp.status_code != 200:
                        err_content = await upstream_resp.aread()
                        decoded_err = err_content.decode("utf-8", errors="replace")
                        yield f"data: {json.dumps({'error': decoded_err})}\n\n"
                        return
                    async for line in upstream_resp.aiter_lines():
                        if line:
                            yield f"{line}\n\n"
            except httpx.RequestError as exc:
                yield f"data: {json.dumps({'error': str(exc)})}\n\n"
            finally:
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
        await client.aclose()
        raise HTTPException(
            status_code=503,
            detail=f"Worker unavailable at {DEFAULT_WORKER_URL}: {exc}",
            headers=correlation_headers,
        ) from exc
    except httpx.RequestError as exc:
        await client.aclose()
        raise HTTPException(
            status_code=502,
            detail=f"Error proxying to worker: {exc}",
            headers=correlation_headers,
        ) from exc
    finally:
        await client.aclose()

    headers = dict(correlation_headers)
    content = (
        upstream_resp.json()
        if upstream_resp.status_code == 200
        else {"error": upstream_resp.text}
    )
    return JSONResponse(
        status_code=upstream_resp.status_code,
        content=content,
        headers=headers,
    )

