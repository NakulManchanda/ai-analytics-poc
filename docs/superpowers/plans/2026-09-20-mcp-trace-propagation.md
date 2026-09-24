# MCP Trace Propagation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Continue one synchronous `ai.run` trace through the app's Streamable HTTP MCP session into safe FastMCP request/tool/resource and DuckDB execution spans, visible in local Jaeger.

**Architecture:** Keep the O1 app tracer as the trace root. The app adapter injects W3C Trace Context into an explicit FastMCP `StreamableHttpTransport` for each session. The independently deployed MCP service owns a separate opt-in/fail-open OTLP runtime; its FastMCP request middleware extracts the incoming HTTP context and makes an `mcp.request` server span current, then nests tool/resource spans beneath it, while narrow query wrappers add DuckDB child spans. Both processes export to the existing Collector, so the Collector and Jaeger remain backend-neutral infrastructure.

**Tech Stack:** Python 3.12, OpenTelemetry Python API/SDK and OTLP/HTTP exporter 1.44.0, W3C Trace Context, FastMCP 2.x Streamable HTTP and middleware, Starlette/uvicorn, DuckDB, Docker Compose, pytest, shell smoke tests, Make.

**Spec:** `docs/decisions/0007-telemetry-metrics-comparison-architecture.md`; GitHub issue #118; baseline design `docs/superpowers/plans/2026-09-19-local-otel-jaeger.md`.

## Global Constraints

- Work only on issue #118, branch `codex/o2-mcp-tracing`, and worktree `.worktrees/o2-mcp-tracing`, based on `fe81b93` / merged PR #117.
- This is synchronous application-to-FastMCP HTTP propagation only. Do not add Redis/job-envelope propagation, worker tracing, or worker configuration.
- The MCP service never calls an LLM; do not add Bedrock, Langfuse, AWS ADOT/X-Ray, Grafana/Prometheus, deployment, or infrastructure changes.
- Preserve Redis/SSE semantic events and the CloudWatch EMF/local JSONL metrics contract exactly; add no EMF or JSONL fields, exporters, or behavior.
- Tracing is opt-in and fail-open in both processes. A disabled exporter, missing endpoint, malformed/missing incoming context, or unreachable Collector must not change the application/MCP success or error contract.
- Export only stable, bounded identifiers and dimensions. Never attach prompts, model outputs, raw SQL, credentials, request headers other than extracting W3C context, complete tool arguments, dataset rows, profile contents, or unrestricted user content.
- Use the shared local Collector and its existing loopback-only Jaeger UI. Do not publish Collector receiver ports.
- Pin direct Python OTEL/uvicorn dependencies and container images. Automated smoke uses its own Compose project and dynamic host ports, and tears down only that project.
- Do not implement production code, commit, push, or open a PR while creating this plan.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `services/app/app/mcp_client.py` | Convert the current span context to W3C `traceparent`/`tracestate` headers and use it for every outbound Streamable HTTP MCP session. |
| `services/app/tests/test_mcp_client.py` | Prove header injection, active/no-active context behavior, and unchanged validation/error behavior without contacting MCP. |
| `services/mcp/mcp_server/telemetry.py` | MCP-local opt-in/fail-open OTLP runtime and FastMCP HTTP-context/tool/resource span middleware. |
| `services/mcp/mcp_server/server.py` | Build the traced FastMCP ASGI app and put small, safe DuckDB child spans around the two governed query entry points. |
| `services/mcp/mcp_server/asgi.py` | Export the `app` ASGI callable used by uvicorn, keeping CLI/server construction out of the tracing module. |
| `services/mcp/tests/test_telemetry.py` | Cover MCP telemetry settings, bootstrap failures, W3C extraction, parentage, safe attributes, and failed handler spans. |
| `services/mcp/tests/test_protocol.py` | Retain the existing MCP contract and add in-memory tool/resource/DuckDB span tests using an injected tracer. |
| `services/mcp/pyproject.toml`, `services/mcp/uv.lock` | Pin MCP-local OTEL SDK/exporter/API and uvicorn runtime dependencies. |
| `services/mcp/Dockerfile` | Start the traced ASGI app under uvicorn rather than the FastMCP CLI-only entrypoint. |
| `docker-compose.observability.yml` | Enable the existing MCP service's optional OTLP export to the shared Collector. |
| `scripts/smoke/16_observability.sh` | Prove one Jaeger trace has the app root and expected MCP/DuckDB descendants while remaining privacy-safe. |
| `docs/work-history/0060-mcp-otel-trace-propagation.md`, `docs/work-history/README.md` | Record #118 scope, decisions, verification evidence, PR state, limitations, and lessons in the next monotonically numbered history entry. |

## Trace and Attribute Contract

The expected successful hierarchy for an analytical tool call is:

```text
ai.run                         service.name=ai-analytics-app
└─ mcp.request                 service.name=analytics-mcp, SpanKind.SERVER
   └─ mcp.tool.execute          mcp.tool.name=query_taxi_data
      └─ duckdb.query           db.system.name=duckdb
```

`mcp.resource.read` replaces `mcp.tool.execute` when the protocol method is `resources/read`; it is also a child of the server request span. Session initialization/list requests may be represented only by `mcp.request`; do not add a generic span for every protocol message.

Allowed attributes are exact stable names and values:

```text
mcp.request:       rpc.system="mcp", rpc.method=<fixed MCP protocol method>
mcp.tool.execute:  mcp.tool.name=<registered fixed tool name>
mcp.resource.read: mcp.resource.uri="dataset://nyc-taxi/schema"
duckdb.query:      db.system.name="duckdb", ai.tool.name=<registered fixed tool name>,
                   ai.analysis=<allowlisted analysis only>, ai.row_limit=<1..20 only>
```

Do not add `http.url`, host, client IP, `traceparent`, `tracestate`, exception message, SQL, resource content, region name, arbitrary tool arguments, query result data, prompt, or model output as attributes. Exceptions are recorded through the SDK exception event/status mechanism only; the existing returned tool error and application exceptions remain unchanged.

## Task 1: Inject W3C context into every app-to-MCP Streamable HTTP session

**Files:**

- Modify: `services/app/app/mcp_client.py`
- Create: `services/app/tests/test_mcp_client.py`

**Interfaces:**

- Consumes: `opentelemetry.propagate.inject(carrier: MutableMapping[str, str]) -> None`, `trace.get_current_span()`.
- Produces: `FastMCPDatasetProfileClient._client() -> Client`, which constructs `Client(StreamableHttpTransport(self._mcp_url, headers=headers))`.
- Produces: `FastMCPDatasetProfileClient._trace_headers() -> dict[str, str]`, returning only propagator-produced W3C context when a valid span context is active, otherwise `{}`.

- [ ] **Step 1: Write failing app adapter tests**

Create `services/app/tests/test_mcp_client.py`. Patch `app.mcp_client.StreamableHttpTransport` and `app.mcp_client.Client`; create an SDK `TracerProvider`/`InMemorySpanExporter`, enter `tracer.start_as_current_span("parent")`, and call `_client()`.

```python
def test_client_injects_active_w3c_trace_context(monkeypatch, tracer):
    client = FastMCPDatasetProfileClient("http://mcp.example/mcp")
    with tracer.start_as_current_span("parent") as parent:
        client._client()
    headers = captured_transport_headers()
    assert headers["traceparent"].split("-")[1] == format(parent.get_span_context().trace_id, "032x")
    assert set(headers) <= {"traceparent", "tracestate"}

def test_client_uses_no_trace_headers_without_an_active_recording_span(monkeypatch):
    FastMCPDatasetProfileClient()._client()
    assert captured_transport_headers() == {}
```

Add a third test that runs `query_taxi_data` with an invalid analysis and asserts `MCPToolError(retryable=False)` before a transport is built; this locks the existing governance/error behavior.

- [ ] **Step 2: Run the new tests to verify red**

Run:

```bash
uv run --project services/app pytest services/app/tests/test_mcp_client.py -q
```

Expected: FAIL because the adapter currently creates `Client(self._mcp_url)` directly and has no injectable transport/header seam.

- [ ] **Step 3: Implement the smallest explicit transport seam**

Import `propagate`, `trace`, and `StreamableHttpTransport`. Add:

```python
def _trace_headers(self) -> dict[str, str]:
    if not trace.get_current_span().get_span_context().is_valid:
        return {}
    headers: dict[str, str] = {}
    propagate.inject(headers)
    return headers

def _client(self) -> Client:
    return Client(StreamableHttpTransport(self._mcp_url, headers=self._trace_headers()))
```

Replace each `async with Client(self._mcp_url)` in the four private async methods with `async with self._client()`. Do not reuse a client/session between calls, add non-W3C headers, or catch/suppress a propagation failure differently from the existing MCP exception mapping.

- [ ] **Step 4: Run focused app regression tests**

Run:

```bash
uv run --project services/app pytest services/app/tests/test_mcp_client.py services/app/tests/test_governed_query.py services/app/tests/test_orchestration_tracing.py -q
```

Expected: PASS. The exporter remains no-op unless the existing app telemetry configuration enables it.

- [ ] **Step 5: Commit the cohesive adapter change**

```bash
git add services/app/app/mcp_client.py services/app/tests/test_mcp_client.py
git commit -m "feat(observability): propagate W3C context to MCP"
```

## Task 2: Add an independent, fail-open MCP tracing runtime and HTTP extraction boundary

**Files:**

- Create: `services/mcp/mcp_server/telemetry.py`
- Create: `services/mcp/tests/test_telemetry.py`
- Modify: `services/mcp/pyproject.toml`
- Modify: `services/mcp/uv.lock`

**Interfaces:**

- Produces: `MCPTelemetrySettings.from_environment() -> MCPTelemetrySettings` with `enabled: bool = False`, `service_name: str = "analytics-mcp"`, `deployment_environment: str = "local"`, and `traces_endpoint: str | None = None`.
- Produces: `MCPTracingRuntime(tracer: trace.Tracer, provider: TracerProvider | None)` from `build_mcp_tracing(settings: MCPTelemetrySettings, *, span_processor: SpanProcessor | None = None) -> MCPTracingRuntime`.
- Produces: `MCPTracingMiddleware(tracer: trace.Tracer)`, a FastMCP `Middleware` which uses FastMCP's `get_http_headers()` only to extract the W3C carrier, starts `mcp.request` as a `SpanKind.SERVER` span in `on_request`, and makes it current while invoking the request handler.
- Consumes: standard `OTEL_TRACING_ENABLED`, `OTEL_SERVICE_NAME`, `OTEL_DEPLOYMENT_ENVIRONMENT`, and `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`; MCP defaults differ only in service name.

- [ ] **Step 1: Write failing runtime and ASGI-boundary tests**

Use `InMemorySpanExporter`, a traced `mcp.http_app(path="/mcp")`, and `httpx.ASGITransport` to complete a Streamable HTTP initialization/request while asserting the current span is valid. Specify:

```python
def test_mcp_telemetry_is_disabled_by_default(monkeypatch):
    assert build_mcp_tracing(MCPTelemetrySettings.from_environment()).provider is None

def test_enabled_mcp_telemetry_without_endpoint_fails_open(monkeypatch, caplog):
    monkeypatch.setenv("OTEL_TRACING_ENABLED", "true")
    runtime = build_mcp_tracing(MCPTelemetrySettings.from_environment())
    assert runtime.provider is None
    assert "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT" in caplog.text

async def test_fastmcp_middleware_extracts_traceparent_and_makes_server_child_current():
    parent_context = make_remote_parent_context()
    response = await call_mcp_asgi(
        build_mcp(tracer=tracer).http_app(path="/mcp"),
        headers=[(b"traceparent", make_traceparent(parent_context).encode())],
    )
    span = only_finished_span()
    assert span.name == "mcp.request"
    assert span.parent.span_id == parent_context.span_id
    assert span.context.trace_id == parent_context.trace_id
    assert span.attributes == {"rpc.system": "mcp", "rpc.method": "unknown"}
```

Cover absent and malformed `traceparent`: each request returns the downstream response and creates an independent valid `mcp.request` root. Cover a downstream exception: it still propagates, and the finished span has `StatusCode.ERROR` plus an exception event. Assert no request headers occur in attributes or serialized exported span data.

- [ ] **Step 2: Verify red and add the direct dependencies**

Run:

```bash
uv run --project services/mcp pytest services/mcp/tests/test_telemetry.py -q
```

Expected: FAIL because MCP telemetry does not exist.

Add exact direct runtime dependencies:

```toml
"opentelemetry-api==1.44.0",
"opentelemetry-sdk==1.44.0",
"opentelemetry-exporter-otlp-proto-http==1.44.0",
"uvicorn[standard]>=0.35,<1",
```

Refresh only the MCP lock:

```bash
uv lock --project services/mcp
```

- [ ] **Step 3: Implement fail-open runtime and ASGI middleware**

Mirror O1's per-process, non-global provider design in `mcp_server.telemetry`; do not import app code. The factory must return an API no-op tracer/provider `None` when disabled, endpoint-less, or initialization throws. Enabled runtime resource attributes are exactly `service.name` and `deployment.environment.name`; use `OTLPSpanExporter(endpoint=settings.traces_endpoint)` and `BatchSpanProcessor` in production, accepting a supplied processor for tests.

Implement the FastMCP request middleware conceptually as:

```python
class MCPTracingMiddleware(Middleware):
    async def on_request(self, context, call_next):
        parent = propagate.extract(get_http_headers())
        with self._tracer.start_as_current_span(
            "mcp.request", context=parent, kind=trace.SpanKind.SERVER
        ) as span:
            span.set_attributes({"rpc.system": "mcp", "rpc.method": "unknown"})
            return await call_next(context)
```

Disable SDK exception recording and automatic exception-derived status descriptions for every MCP span. On a handler failure, explicitly set `StatusCode.ERROR` with no description, then re-raise the original exception. Do not set `http.*` attributes or save headers. The same middleware updates `rpc.method` only from its fixed protocol method before child spans are created.

- [ ] **Step 4: Run focused MCP telemetry tests**

Run:

```bash
uv run --project services/mcp pytest services/mcp/tests/test_telemetry.py -q
uv run --project services/mcp ruff check mcp_server tests
uv run --project services/mcp black --check mcp_server tests
```

Expected: PASS.

- [ ] **Step 5: Commit the MCP runtime layer**

```bash
git add services/mcp/mcp_server/telemetry.py services/mcp/tests/test_telemetry.py services/mcp/pyproject.toml services/mcp/uv.lock
git commit -m "feat(observability): add fail-open MCP OTEL runtime"
```

## Task 3: Add safe FastMCP protocol and DuckDB child spans without changing tool contracts

**Files:**

- Modify: `services/mcp/mcp_server/telemetry.py`
- Modify: `services/mcp/mcp_server/server.py`
- Modify: `services/mcp/tests/test_protocol.py`
- Modify: `services/mcp/tests/test_telemetry.py`

**Interfaces:**

- Produces: the `MCPTracingMiddleware(tracer: trace.Tracer)` defined in Task 2, with `on_request`, `on_call_tool`, and `on_read_resource` overrides.
- Produces: `build_mcp(..., tracer: trace.Tracer | None = None) -> FastMCP`; it registers `MCPTracingMiddleware` only through the injected/no-op tracer path and otherwise preserves the existing tools/resources/lifespan.
- Produces: `run_pinned_query(..., tracer: trace.Tracer | None = None)` and `run_pinned_average_trip_metrics(..., tracer: trace.Tracer | None = None)` (or equivalent closures injected by `build_mcp`) whose `duckdb.query` span surrounds only the governed `dataset_spike.analytics` call.

- [ ] **Step 1: Write failing semantic span tests alongside the existing protocol test**

Create an SDK provider/exporter, inject its tracer into `build_mcp`, and use FastMCP's in-memory `Client(build_mcp(...))`. For the HTTP parentage test, exercise the traced ASGI app in Task 4; the in-memory tests here assert semantics and safety independently.

```python
def test_governed_query_creates_safe_tool_and_duckdb_spans(tracer):
    result = asyncio.run(call_query_over_in_memory_fastmcp(tracer))
    spans = spans_by_name()
    assert spans["mcp.tool.execute"].attributes == {"mcp.tool.name": "query_taxi_data"}
    assert spans["duckdb.query"].attributes == {
        "db.system.name": "duckdb",
        "ai.tool.name": "query_taxi_data",
        "ai.analysis": "top_pickup_zones",
        "ai.row_limit": 2,
    }
    assert spans["duckdb.query"].parent.span_id == spans["mcp.tool.execute"].context.span_id
    assert "Alpha" not in exported_span_json()
```

Also test `resources/read` emits `mcp.resource.read` with the fixed schema URI and no resource body, and an injected query runner failure records error status on both the DuckDB and tool spans while FastMCP still reports the same tool failure behavior. Use a deliberate sentinel SQL/prompt-like text in the test runner/arguments and assert it is absent from serialized spans.

- [ ] **Step 2: Verify red**

Run:

```bash
uv run --project services/mcp pytest services/mcp/tests/test_protocol.py services/mcp/tests/test_telemetry.py -q
```

Expected: FAIL because no FastMCP middleware or DuckDB spans exist.

- [ ] **Step 3: Implement only the bounded child spans**

In `MCPTracingMiddleware.on_request`, set `rpc.method` on the active request span only if `context.method` is one of the fixed protocol methods `tools/call`, `resources/read`, `initialize`, `tools/list`, or `resources/list`; otherwise retain `unknown`. Do not derive values from request payloads.

Implement child spans as:

```python
async def on_call_tool(self, context, call_next):
    tool_name = context.message.name
    with self._tracer.start_as_current_span("mcp.tool.execute") as span:
        span.set_attribute("mcp.tool.name", tool_name)
        return await call_next(context)

async def on_read_resource(self, context, call_next):
    with self._tracer.start_as_current_span("mcp.resource.read") as span:
        span.set_attribute("mcp.resource.uri", SCHEMA_RESOURCE_URI)
        return await call_next(context)
```

For a query wrapper, create `duckdb.query` as current immediately around `query_dataset`/`query_average_trip_metrics`, set `db.system.name`, the fixed registered tool name, and only allowlisted `analysis` plus validated numeric `limit`; omit `ai.analysis`/`ai.row_limit` for average metrics rather than storing `region_name`. Do not put `query_id` on the span because it is only available after execution and a failing query must still have one stable attribute contract. Let thrown exceptions escape unchanged so the SDK marks the active spans in error.

Keep `get_dataset_profile` uninstrumented beyond its enclosing request: it does not execute DuckDB in the request path. Do not alter tool registration names, validation messages, resource JSON, data paths, timeout, cache lifecycle, or return payloads.

- [ ] **Step 4: Run MCP focused and regression verification**

Run:

```bash
uv run --project services/mcp pytest services/mcp/tests/test_protocol.py services/mcp/tests/test_telemetry.py -q
uv run --project services/mcp pytest services/mcp/tests -q
uv run --project services/mcp ruff check mcp_server tests
uv run --project services/mcp black --check mcp_server tests
```

Expected: PASS; the existing protocol contract remains byte-for-byte compatible.

- [ ] **Step 5: Commit semantic MCP spans**

```bash
git add services/mcp/mcp_server/telemetry.py services/mcp/mcp_server/server.py services/mcp/tests/test_protocol.py services/mcp/tests/test_telemetry.py
git commit -m "feat(observability): trace MCP tools and DuckDB queries"
```

## Task 4: Serve the traced MCP ASGI application and wire the Compose overlay

**Files:**

- Create: `services/mcp/mcp_server/asgi.py`
- Modify: `services/mcp/Dockerfile`
- Modify: `docker-compose.observability.yml`
- Modify: `services/mcp/tests/test_telemetry.py`

**Interfaces:**

- Produces: `services/mcp/mcp_server/asgi.py:app`, the `FastMCP.http_app(path="/mcp")` ASGI application with extraction supplied by the registered FastMCP middleware.
- Produces: module-level `tracing_runtime = build_mcp_tracing(MCPTelemetrySettings.from_environment())` and `mcp = build_mcp(tracer=tracing_runtime.tracer)` in the ASGI entrypoint.
- Consumes: the existing compose service DNS endpoint `http://otel-collector:4318/v1/traces`.

- [ ] **Step 1: Write failing ASGI integration tests**

With the test tracer and `httpx.ASGITransport`, call the MCP Streamable HTTP endpoint using a generated remote W3C `traceparent`, complete initialization, then invoke `query_taxi_data`. Assert all finished spans have the supplied trace ID and the parent chain is exactly `mcp.request → mcp.tool.execute → duckdb.query`; assert app/mcp resource service names are not accidentally shared.

Add a no-endpoint test importing/reloading `mcp_server.asgi` with tracing disabled: a protocol request succeeds and no network/exporter construction occurs.

- [ ] **Step 2: Verify red**

Run:

```bash
uv run --project services/mcp pytest services/mcp/tests/test_telemetry.py -q
```

Expected: FAIL because the MCP image invokes FastMCP's CLI path and no traced ASGI callable exists.

- [ ] **Step 3: Add the ASGI entrypoint and change only MCP service startup**

In `asgi.py`, construct the runtime once, inject its tracer into `build_mcp`, and construct `app` via `mcp.http_app(path="/mcp")`. The FastMCP request middleware registered by `build_mcp` performs extraction, so preserve FastMCP's own request-context middleware and lifespan without adding an outer tracing middleware.

Update only the MCP Docker `CMD` to:

```json
["uv", "run", "--project", "services/mcp", "--no-sync", "uvicorn", "mcp_server.asgi:app", "--host", "0.0.0.0", "--port", "8001"]
```

Extend only the `mcp` service in `docker-compose.observability.yml`:

```yaml
  mcp:
    environment:
      OTEL_TRACING_ENABLED: "true"
      OTEL_SERVICE_NAME: analytics-mcp
      OTEL_DEPLOYMENT_ENVIRONMENT: local-compose
      OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: http://otel-collector:4318/v1/traces
```

Do not add these variables to the base compose file, worker, or any AWS/ECS definition.

- [ ] **Step 4: Validate rendered configuration and MCP startup**

Run:

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml config --quiet
COMPOSE_PROJECT_NAME=ai-analytics-o2-config WEB_PORT=0 JAEGER_UI_PORT=0 \
  docker compose -f docker-compose.yml -f docker-compose.observability.yml up --build -d mcp otel-collector jaeger
COMPOSE_PROJECT_NAME=ai-analytics-o2-config \
  docker compose -f docker-compose.yml -f docker-compose.observability.yml ps
COMPOSE_PROJECT_NAME=ai-analytics-o2-config \
  docker compose -f docker-compose.yml -f docker-compose.observability.yml down --remove-orphans
```

Expected: rendered configuration is valid, `mcp` becomes healthy, and the Collector exposes no host port.

- [ ] **Step 5: Commit serving and local wiring**

```bash
git add services/mcp/mcp_server/asgi.py services/mcp/Dockerfile docker-compose.observability.yml services/mcp/tests/test_telemetry.py
git commit -m "feat(observability): export MCP traces to local Collector"
```

## Task 5: Extend the isolated Jaeger smoke proof to verify one distributed trace

**Files:**

- Modify: `scripts/smoke/16_observability.sh`
- Modify: `Makefile` only if its existing `observability-smoke` target does not already invoke this script unchanged

**Interfaces:**

- Consumes: existing `ai.run` smoke request/Jaeger query flow and Compose variable contract (`COMPOSE_PROJECT_NAME`, `WEB_PORT=0`, `JAEGER_UI_PORT=0`).
- Produces: a process exit status of zero only when the matched trace contains safe app and MCP descendants with one shared trace ID.

- [ ] **Step 1: Make the smoke assertion fail against O1**

Extend the final embedded Python assertion to locate the `ai.run` span by `ai.run_id`, take its `traceId`, and assert that the same trace has:

```python
required_names = {"ai.run", "mcp.request", "mcp.resource.read", "mcp.tool.execute", "duckdb.query"}
assert required_names <= {span["name"] for span in trace_spans}
assert all(span["traceId"] == ai_run["traceId"] for span in required_spans)
assert parent_id("mcp.request") == ai_run["spanId"]
assert parent_id("mcp.tool.execute") == span_id("mcp.request")
assert parent_id("duckdb.query") == span_id("mcp.tool.execute")
```

For the resource read, assert its parent is an `mcp.request` with the same trace ID (initialization and schema lookup can use distinct HTTP request spans). Validate `service.name` resource values include both `ai-analytics-app` and `analytics-mcp`; validate only the attribute contract from the table above. Retain the distinctive prompt absence check and add a distinctive fake dataset row sentinel from the request result fixture; assert neither occurs anywhere in Jaeger JSON.

- [ ] **Step 2: Run it to prove red before the MCP wiring lands**

Run:

```bash
make observability-smoke
```

Expected: FAIL on the missing `mcp.request`, `mcp.tool.execute`, and `duckdb.query` spans; its cleanup trap still removes only the generated Compose project.

- [ ] **Step 3: Finish the minimal trace-tree assertion**

Do not add fixed sleeps, a second collector, static host ports, remote queries, AWS credentials, or raw span dumps. Keep the existing sixty one-second bounded polling loop, dynamically resolved ports, and failure-only Compose logs. Ensure the script uses Jaeger's returned IDs rather than ordering assumptions, because app-to-MCP initialization/resource/tool calls are separate HTTP requests.

- [ ] **Step 4: Run the end-to-end smoke and Make dry-run**

Run:

```bash
make observability-smoke
make -n observability-up
make -n observability-down
```

Expected: PASS. Output identifies temporary app/Jaeger URLs and the `run_id`; no prompt, fake row sentinel, SQL, or credentials appears in queried trace JSON.

- [ ] **Step 5: Commit the acceptance proof**

```bash
git add scripts/smoke/16_observability.sh Makefile
git commit -m "test(observability): prove MCP trace propagation"
```

## Task 6: Document the O2 boundary and create the required work-history entry

**Files:**

- Create: `docs/work-history/0060-mcp-otel-trace-propagation.md`
- Modify: `docs/work-history/README.md`

**Interfaces:**

- Produces: the next monotonically numbered work-history record for issue #118 and its draft PR URL/state once created by the executor.
- Consumes: verified commands/outcomes from Tasks 1–5 and ADR-0007's signal-ownership/privacy constraints.

- [ ] **Step 1: Write the work-history record with exact operational boundaries**

Use the established work-history structure. Record:

- goal and starting point: O1/PR #117 merged at `fe81b93`;
- decision: W3C propagation through explicit Streamable HTTP transport; separate MCP SDK runtime; ASGI extraction; FastMCP/DuckDB child spans;
- exact safe attributes and explicit omission of prompts, SQL, arguments, rows, secrets, and outputs;
- verification commands and outcomes, including focused app/MCP tests, format/lint, rendered Compose check, and `make observability-smoke`;
- PR number/URL and merge state (draft until the actual review/merge occurs);
- limitations: no Redis/worker propagation, Bedrock/Langfuse/AWS export, dashboards, OTEL logs/metrics, or EMF/JSONL modification.

Use only repository-relative paths and no machine-specific values or live endpoint tokens.

- [ ] **Step 2: Add the index row**

Insert a `0060` row in the Markdown table in `docs/work-history/README.md`, linking to the entry and describing “MCP W3C trace propagation and DuckDB child spans”; use the actual PR state, not a predicted merged state.

- [ ] **Step 3: Run documentation safety checks**

Run:

```bash
rg -n '/Users/|traceparent: [0-9a-f]{2}-|sk-|AKIA|LANGFUSE|AWS_SECRET' \
  docs/work-history/0060-mcp-otel-trace-propagation.md docs/work-history/README.md \
  services/app/app/mcp_client.py services/mcp/mcp_server docker-compose.observability.yml scripts/smoke/16_observability.sh
git diff --check
```

Expected: no matches and no whitespace errors.

- [ ] **Step 4: Commit documentation**

```bash
git add docs/work-history/0060-mcp-otel-trace-propagation.md docs/work-history/README.md
git commit -m "docs(observability): record MCP trace propagation"
```

## Task 7: Final proportional verification and draft PR handoff

**Files:**

- Verify only; no new implementation files.

- [ ] **Step 1: Run the full scoped test, style, Compose, and smoke suite**

Run:

```bash
uv run --project services/app pytest services/app/tests/test_mcp_client.py services/app/tests/test_governed_query.py services/app/tests/test_orchestration_tracing.py -q
uv run --project services/mcp pytest services/mcp/tests -q
uv run --project services/app ruff check app tests
uv run --project services/app black --check app tests
uv run --project services/mcp ruff check mcp_server tests
uv run --project services/mcp black --check mcp_server tests
docker compose -f docker-compose.yml -f docker-compose.observability.yml config --quiet
make observability-smoke
```

Expected: every command exits zero. If an existing unrelated suite failure occurs, capture the exact command and output in the work-history/PR rather than changing unrelated behavior.

- [ ] **Step 2: Inspect scope, contracts, and secret safety**

Run:

```bash
git diff fe81b93...HEAD --check
git status --short
git diff fe81b93...HEAD -- services/app/app/mcp_client.py services/mcp/mcp_server services/mcp/Dockerfile docker-compose.observability.yml scripts/smoke/16_observability.sh docs/work-history
rg -n 'prompt|sql|rows|arguments|authorization|secret|password|token' services/mcp/mcp_server/telemetry.py services/app/app/mcp_client.py
```

Expected: all changed production telemetry attributes conform to the contract; no worker/Redis, Bedrock/Langfuse/AWS, or EMF/JSONL files were changed; no sensitive content is an attribute value.

- [ ] **Step 3: Create the required early draft PR after the first coherent pushed commit, then update it**

Follow the project workflow: push `codex/o2-mcp-tracing` after Task 1 or Task 2 is reviewable, open a draft PR linked to #118, and keep its description current with scope, the exact trace tree, safe attributes, verification evidence, and limitations. Do not merge, deploy, apply infrastructure, or tag.

- [ ] **Step 4: Request the independent project PR review**

Use `.claude/skills/project-pr-review/SKILL.md` for the independent review, exact-head GitHub Actions timing, validation, re-review mechanics, and mergeability determination. Address only in-scope actionable findings on the same branch, re-run the affected verification, update work history/PR evidence, and return the PR ready for the coordinator's gated merge decision.

## Self-Review

**Spec coverage:** Task 1 covers outbound standard W3C injection. Tasks 2 and 4 cover separate MCP fail-open OTLP bootstrap and inbound extraction/continued trace. Task 3 covers safe request/tool/resource/DuckDB spans and failure status without contract changes. Task 4 covers Compose MCP export. Task 5 proves the Jaeger hierarchy with isolation and privacy assertions. Task 6 fulfills work history. Global constraints and Task 7 explicitly preserve metrics/events and exclude Redis/worker, Bedrock, Langfuse, AWS, and EMF/JSONL work.

**Placeholder scan:** No TBD/TODO or deferred implementation placeholders are present; each task supplies specific files, interfaces, test assertions, red/green commands, and commit boundaries.

**Type consistency:** `MCPTelemetrySettings`, `MCPTracingRuntime`, `build_mcp_tracing`, `MCPTracingMiddleware`, `build_mcp(..., tracer=...)`, `mcp_server.asgi:app`, and `_trace_headers()` use the same names across producing and consuming tasks.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-20-mcp-trace-propagation.md`. Execute it task-by-task with either `superpowers:subagent-driven-development` or `superpowers:executing-plans`; do not expand scope beyond #118.
