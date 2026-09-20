# Local OTEL Collector and Jaeger Trace Skeleton Implementation Plan

> **For agentic workers:** Execute this plan one task at a time with red/green verification. Do not expand the scope beyond issue #116 or ADR-0007.

**Goal:** Add a small, local OpenTelemetry trace path for one synchronous AI request so a developer can run the application, make a request, and inspect its `ai.run` span in Jaeger.

**Architecture:** The application creates an explicit semantic `ai.run` span and exports it over OTLP/HTTP to a local OpenTelemetry Collector. The Collector batches and forwards traces over OTLP/gRPC to Jaeger. Jaeger supplies transient local trace storage and its trace-search UI. OpenTelemetry is disabled by default and fails open; no observability outage may fail an application request.

**Tech stack:** Python 3.12, FastAPI, OpenTelemetry Python SDK and OTLP HTTP exporter, OpenTelemetry Collector, Jaeger 2, Docker Compose, pytest, shell smoke tests, Make.

**Specification:** `docs/decisions/0007-telemetry-metrics-comparison-architecture.md`

## Global constraints

- Work only in issue #116, branch `codex/o1-local-otel-jaeger`, and worktree `.worktrees/o1-local-otel-jaeger`.
- Do not add Kubernetes, Grafana, Prometheus, Langfuse wiring, AWS deployment, automatic HTTP instrumentation, log export, metric export, or cross-worker trace propagation in O1.
- Never include prompts, model responses, SQL, credentials, or environment-variable values in span attributes.
- Preserve existing CloudWatch EMF/JSONL run metrics. Tracing supplements them; it does not replace them.
- Keep the Collector between the application and Jaeger so the application remains backend-neutral.
- Pin container images and Python dependencies.
- Use an isolated Compose project and dynamically allocated host ports in automated smoke tests.

---

## Task 1: Add telemetry configuration and a backend-neutral tracer factory

**Files:**

- Create: `services/app/app/telemetry.py`
- Create: `services/app/tests/test_telemetry.py`
- Modify: `services/app/pyproject.toml`
- Modify: `uv.lock`

### Step 1: Write failing configuration tests

Add tests proving:

```python
def test_telemetry_is_disabled_by_default(monkeypatch): ...
def test_telemetry_reads_standard_service_and_otlp_environment(monkeypatch): ...
def test_enabled_telemetry_without_an_endpoint_falls_back_to_noop(monkeypatch, caplog): ...
```

Expected configuration contract:

```python
@dataclass(frozen=True)
class TelemetrySettings:
    enabled: bool = False
    service_name: str = "ai-analytics-app"
    deployment_environment: str = "local"
    traces_endpoint: str | None = None

    @classmethod
    def from_environment(cls) -> "TelemetrySettings": ...
```

Environment variables:

- `OTEL_TRACING_ENABLED`
- `OTEL_SERVICE_NAME`
- `OTEL_DEPLOYMENT_ENVIRONMENT`
- `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`

Run:

```bash
uv run --project services/app pytest services/app/tests/test_telemetry.py -q
```

Expected: FAIL because the module does not exist.

### Step 2: Add direct OpenTelemetry dependencies

Add compatible pinned constraints for:

```toml
"opentelemetry-api==1.44.0",
"opentelemetry-sdk==1.44.0",
"opentelemetry-exporter-otlp-proto-http==1.44.0",
```

Refresh the lockfile with:

```bash
uv lock
```

### Step 3: Implement the minimal factory

Implement a small runtime holder:

```python
@dataclass(frozen=True)
class TracingRuntime:
    tracer: trace.Tracer
    provider: TracerProvider | None


def build_tracing(
    settings: TelemetrySettings,
    *,
    span_processor: SpanProcessor | None = None,
) -> TracingRuntime: ...
```

Behavior:

- Disabled returns an API no-op tracer and no provider.
- Enabled without a trace endpoint logs one warning and returns a no-op tracer.
- Enabled creates a `TracerProvider` with resource attributes `service.name` and `deployment.environment.name`.
- Production uses `OTLPSpanExporter` and `BatchSpanProcessor`; tests may inject `SimpleSpanProcessor`.
- Construction errors are logged and fall back to no-op so telemetry cannot stop application startup.
- Do not set a process-global provider; inject the returned tracer into the orchestration loop.

### Step 4: Run tests and commit

```bash
uv run --project services/app pytest services/app/tests/test_telemetry.py -q
git add services/app/app/telemetry.py services/app/tests/test_telemetry.py services/app/pyproject.toml uv.lock
git commit -m "feat(observability): add OTEL tracing bootstrap"
```

Expected: telemetry tests pass.

---

## Task 2: Add the safe `ai.run` semantic span

**Files:**

- Create: `services/app/tests/test_orchestration_tracing.py`
- Modify: `services/app/app/orchestration/loop.py`

### Step 1: Write a failing in-memory span test

Construct an SDK provider with `InMemorySpanExporter`, inject its tracer into `OrchestrationLoop`, run the existing fake/success path, and assert exactly one root span with:

```text
name = ai.run
ai.run_id = <generated run id>
ai.conversation_id = <generated conversation id>
ai.turn_type = text
ai.status = completed
gen_ai.request.model = <configured model id>
```

Also assert the attribute keys and serialized values contain neither the prompt nor response text.

Add a second test where execution raises and assert the span has error status and an exception event while the original exception still propagates.

Run:

```bash
uv run --project services/app pytest services/app/tests/test_orchestration_tracing.py -q
```

Expected: FAIL because `OrchestrationLoop` does not accept or create spans from an injected tracer.

### Step 2: Instrument the synchronous orchestration seam

Extend the constructor with an optional tracer:

```python
self._tracer = tracer or trace.get_tracer("ai_analytics_poc.orchestration")
```

Wrap `run()` in:

```python
with self._tracer.start_as_current_span("ai.run") as span:
    submission = self.prepare_run(prompt, conversation_id)
    span.set_attributes({...safe identifiers and bounded dimensions...})
    result = self.execute(submission, budgets=budgets)
    span.set_attribute("ai.status", result.status)
    return result
```

On exceptions, record the exception, set OpenTelemetry error status, and re-raise. Do not attach the prompt, model response, SQL, tool payload, or stack-local secrets.

### Step 3: Run focused and orchestration regression tests

```bash
uv run --project services/app pytest services/app/tests/test_orchestration_tracing.py -q
uv run --project services/app pytest services/app/tests/test_orchestration_loop.py services/app/tests/test_ask_api.py -q
git add services/app/app/orchestration/loop.py services/app/tests/test_orchestration_tracing.py
git commit -m "feat(observability): trace synchronous AI runs"
```

Expected: all commands pass.

---

## Task 3: Wire tracing into application startup

**Files:**

- Modify: `services/app/app/main.py`
- Modify: `services/app/tests/test_main.py` or the nearest existing application-factory test

### Step 1: Write a failing wiring test

Test that `create_app(..., tracer=injected_tracer)` passes that tracer to the orchestration loop and that a request through `/api/ask` produces the in-memory `ai.run` span.

Run:

```bash
uv run --project services/app pytest services/app/tests/test_main.py -q
```

Expected: FAIL because the factory has no tracer seam.

### Step 2: Build and inject the runtime tracer

- Add a `tracer` keyword argument to `create_app`.
- Pass it into `OrchestrationLoop`.
- At module startup, build one `TracingRuntime` from `TelemetrySettings.from_environment()` and supply its tracer to the application factory.
- Keep tests deterministic: an unset `OTEL_TRACING_ENABLED` must not start an exporter or network activity.
- Register provider shutdown/flush using the supported SDK lifecycle without introducing a second global provider.

### Step 3: Verify and commit

```bash
uv run --project services/app pytest services/app/tests/test_main.py services/app/tests/test_ask_api.py services/app/tests/test_orchestration_tracing.py -q
git add services/app/app/main.py services/app/tests/test_main.py
git commit -m "feat(observability): wire tracing into app startup"
```

Expected: all focused tests pass.

---

## Task 4: Add the Collector and Jaeger Compose overlay

**Files:**

- Create: `docker-compose.observability.yml`
- Create: `observability/otel-collector.yaml`

### Step 1: Define the expected rendered Compose contract

Before starting containers, use a configuration assertion that checks the rendered model contains:

- `otel-collector`
- `jaeger`
- the pinned images
- app environment pointing to `http://otel-collector:4318/v1/traces`
- Jaeger UI bound to loopback
- no host publishing of Collector OTLP ports

The intended overlay is:

```yaml
services:
  app:
    environment:
      OTEL_TRACING_ENABLED: "true"
      OTEL_SERVICE_NAME: ai-analytics-app
      OTEL_DEPLOYMENT_ENVIRONMENT: local-compose
      OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: http://otel-collector:4318/v1/traces

  otel-collector:
    image: ghcr.io/open-telemetry/opentelemetry-collector-releases/opentelemetry-collector:0.157.0
    command: ["--config=/etc/otelcol/config.yaml"]
    volumes:
      - ./observability/otel-collector.yaml:/etc/otelcol/config.yaml:ro
    depends_on:
      - jaeger

  jaeger:
    image: cr.jaegertracing.io/jaegertracing/jaeger:2.21.0
    ports:
      - "127.0.0.1:${JAEGER_UI_PORT:-16686}:16686"
```

### Step 2: Configure the trace-only Collector pipeline

Use OTLP gRPC and HTTP receivers, `memory_limiter` and `batch` processors, and an OTLP exporter targeting `jaeger:4317` with TLS disabled on the internal Compose network. Define only a traces pipeline for O1.

### Step 3: Render and start the overlay

```bash
docker compose -f docker-compose.yml -f docker-compose.observability.yml config --quiet
COMPOSE_PROJECT_NAME=ai-analytics-o1-check WEB_PORT=0 JAEGER_UI_PORT=0 \
  docker compose -f docker-compose.yml -f docker-compose.observability.yml up --build -d
```

Inspect service state and logs. Tear down only this exact project:

```bash
COMPOSE_PROJECT_NAME=ai-analytics-o1-check \
  docker compose -f docker-compose.yml -f docker-compose.observability.yml down --remove-orphans
```

### Step 4: Commit

```bash
git add docker-compose.observability.yml observability/otel-collector.yaml
git commit -m "feat(observability): add local Collector and Jaeger"
```

---

## Task 5: Add repeatable Make targets and an end-to-end smoke test

**Files:**

- Create: `scripts/smoke/16_observability.sh`
- Modify: `Makefile`

### Step 1: Write the smoke test first

The script must:

1. Generate its own Compose project name.
2. Request dynamic host ports with `WEB_PORT=0` and `JAEGER_UI_PORT=0`.
3. Start `docker-compose.yml` plus `docker-compose.observability.yml`.
4. Resolve the assigned ports with `docker compose port`.
5. Wait for `/api/status` and the Jaeger API.
6. POST a distinctive prompt to `/api/ask` using the existing fake local model path.
7. Poll Jaeger until service `ai-analytics-app` and operation `ai.run` appear.
8. Validate the span contains the safe run/conversation/status attributes.
9. Validate the distinctive prompt is absent from trace JSON.
10. Always tear down only its own Compose project in a shell trap.

Run it before the Make targets exist:

```bash
./scripts/smoke/16_observability.sh
```

Expected: FAIL until the overlay and application wiring are complete, then pass.

### Step 2: Add manual-development Make targets

Add:

```make
observability-up:
	WEB_PORT=$${WEB_PORT:-3000} JAEGER_UI_PORT=$${JAEGER_UI_PORT:-16686} \
		docker compose -f docker-compose.yml -f docker-compose.observability.yml up --build -d

observability-down:
	docker compose -f docker-compose.yml -f docker-compose.observability.yml down

observability-smoke:
	./scripts/smoke/16_observability.sh
```

Include the targets in Make help output if the Makefile has a help convention.

### Step 3: Verify and commit

```bash
make observability-smoke
make -n observability-up
make -n observability-down
git add Makefile scripts/smoke/16_observability.sh
git commit -m "test(observability): add local trace smoke workflow"
```

Expected: the smoke reports the app URL, Jaeger URL, and discovered `ai.run` trace; dry runs show the exact two Compose files.

---

## Task 6: Document the developer workflow and current observability boundary

**Files:**

- Modify: `README.md`
- Modify: `docs/progress.md`
- Create: `docs/work-history/0057-local-otel-jaeger-trace-skeleton.md`
- Modify: `docs/work-history/README.md`

### Step 1: Add concise operating instructions

Document:

```bash
make observability-up
curl -X POST http://localhost:3000/api/ask \
  -H 'content-type: application/json' \
  -d '{"prompt":"How many active customers do we have?"}'
open http://localhost:16686
make observability-down
```

Explain that:

- Jaeger runs adjacent to the app as a container and stores traces transiently for local development.
- The Collector is the stable OTLP ingestion/routing layer.
- Jaeger answers “where did this request spend time?”
- Existing EMF/JSONL metrics answer aggregate AI latency/token/cost questions.
- General endpoint RED metrics, Prometheus/Grafana dashboards, logs-to-OTEL, async trace propagation, Langfuse, and AWS exporters remain later slices.
- No AWS login is needed for the fake-model local stack; the AWS overlay continues to use the developer's configured profile separately.

### Step 2: Record work history and progress

Record issue #116, the architectural boundary, exact verification commands/outcomes, PR state, limitations, and lessons. Keep all committed paths portable.

### Step 3: Check documentation and commit

```bash
rg -n '/Users/|LANGFUSE_SECRET_KEY|sk-lf-' README.md docs docker-compose.observability.yml observability services/app scripts/smoke/16_observability.sh
git diff --check
git add README.md docs/progress.md docs/work-history/0057-local-otel-jaeger-trace-skeleton.md docs/work-history/README.md
git commit -m "docs(observability): explain the local trace workflow"
```

Expected: the secret/path scan returns no matches and `git diff --check` succeeds.

---

## Task 7: Final verification, review, and draft PR

### Step 1: Run the full proportional verification set

```bash
uv run --project services/app pytest services/app/tests -q
uv run --project services/mcp pytest services/mcp/tests -q
docker compose -f docker-compose.yml -f docker-compose.observability.yml config --quiet
make observability-smoke
git diff --check
git status --short
```

Expected:

- App and MCP suites pass.
- Compose renders.
- A real `ai.run` trace reaches Jaeger.
- Worktree contains only intended changes.

### Step 2: Inspect the exact diff and scan for secrets

```bash
git diff main...HEAD --stat
git diff main...HEAD
git grep -n -E 'sk-lf-|LANGFUSE_SECRET_KEY=[^$]|AWS_SECRET_ACCESS_KEY=[^$]' HEAD -- . ':!uv.lock'
```

Expected: no credentials, prompts, or private developer paths are committed.

### Step 3: Push and open a draft PR

```bash
git push -u origin codex/o1-local-otel-jaeger
gh pr create --draft \
  --title "feat: add local OTEL and Jaeger trace skeleton" \
  --body-file <prepared-pr-body>
```

The PR body must reference issue #116 and ADR-0007, list the verification commands, explain the Collector/Jaeger responsibilities, and explicitly list deferred work.

### Step 4: Independent review and issue handoff

- Run the project PR-review workflow after CI is attached to the exact head.
- Address only in-scope findings in the same branch and PR.
- Add a concise issue #116 comment containing the PR URL, verification evidence, limitations, and next recommended slice.
- Do not merge, deploy, or apply infrastructure without satisfying the repository gates and the required user decision.
