# Work history 0071 — Scenario generator and replayer with Prometheus metrics delta scraper (#115 Slice D′)

## Goal

Provide an automated, declarative traffic generation and measurement harness for the inference track
(#115 Slice D′, #122, #123). Replace ad-hoc curl and burst scripts with reproducible scenario specifications,
controlled concurrent asynchronous replay, and worker Prometheus `/metrics` delta snapshotting to accurately
measure vLLM prefix cache hit rates, queue times, throughput, and TTFT under structured analytical load.

## Starting point

- Slices A (#137), B (#138), C′ (#140), and #139 (#141) established governed DuckDB tools, honest model calls,
  prefix partitioning contracts, query catalogue (`query_catalogue.py`), and configurable `AGENT_STRATEGY: manual | crewai`.
- Traffic generation was limited to manual web UI queries, single-request curl scripts, or randomized burst traffic (`burst_traffic.py`).
- No declarative scenario schema existed.
- Worker vLLM Prometheus counters (`vllm:prefix_cache_hits_total`, `vllm:prefix_cache_queries_total`, etc.)
  were only visible as cumulative lifetime totals in Grafana or raw Prometheus scrapes without per-scenario delta attribution.

## Decisions

- **D-115D-1: Declarative Pydantic scenario schema.** Scenarios are defined declaratively in JSON
  (`ScenarioConfig`, `ScenarioConversation`, `ScenarioTurn`) specifying target endpoint type (`app_runs` vs `gateway_chat`),
  concurrency, execution strategy, and ordered turns.
- **D-115D-2: Query catalogue alignment for deterministic replay.** All pre-canned benchmark scenarios
  use questions from `query_catalogue.py` so tool selection is deterministic (`tool_source="catalogue"`), preventing
  LLM proposal variances from injecting noise into load and cache benchmarking.
- **D-115D-3: Asynchronous concurrent replay with conversation preservation.** `ScenarioReplayer` executes
  conversations concurrently up to a configurable semaphore bound while maintaining strictly sequential turn progression
  within each conversation so conversational prefix extensions grow turn-over-turn.
- **D-115D-4: Resilient dual-adapter execution (SSE stream with polling fallback).** For `/api/runs`,
  the replayer primarily consumes real-time SSE streams (`/api/runs/{id}/events`) to detect terminal states and measure
  client-side TTFT; if SSE is unavailable or interrupted, it automatically falls back to polling `/api/conversations/{id}`.
- **D-115D-5: Standard-library Prometheus exposition parser.** Developed a zero-dependency Prometheus text
  exposition parser supporting counters, gauges, histograms (sum/count), labels, and alternative name conventions (colon vs underscore).
- **D-115D-6: Strict pre/post delta isolation and reset detection.** Scrapes worker `/metrics` immediately before
  and after scenario bursts to calculate true attribution vectors ($\Delta \text{hits}$, $\Delta \text{queries}$,
  $\text{hit rate } \% = \Delta \text{hits} / \Delta \text{queries}$), with explicit detection and flagging of counter resets.
- **D-115D-7: Artifact evidence generation.** The CLI entrypoint `services/app/scripts/run_scenario.py` automatically
  writes structured JSON evidence and formatted Markdown tables to `metrics/evidence/`.

## What changed

- **`services/app/app/scenarios/models.py`**:
  - Defined `ScenarioTurn`, `ScenarioConversation`, and `ScenarioConfig` with turn validation and metrics aggregation properties.
- **`services/app/app/scenarios/loader.py`**:
  - Implemented `load_scenario` supporting named resolution and file paths.
  - Implemented `validate_catalogue_alignment` to verify scenario turns against `query_catalogue.py`.
- **`config/scenarios/*.json`**:
  - `shared_prefix_fanout.json`: 20 independent conversations testing global prefix cache hit rate.
  - `growing_multi_turn.json`: 1 conversation progressing through 5 sequential catalogue turns testing conversational prefix extension.
  - `strategy_comparison.json`: 3-turn question sequence for comparing `manual` vs `crewai` strategies.
  - `concurrent_contention.json`: 4 concurrent multi-turn conversations testing queueing and KV cache eviction.
- **`services/app/app/benchmarks/metrics_scraper.py`**:
  - Implemented `PrometheusMetricSnapshot` parser for Prometheus exposition text.
  - Implemented `compute_metrics_delta` with monotonic counter checking and ratio calculations.
- **`services/app/app/benchmarks/replayer.py`**:
  - Implemented `ScenarioReplayer` with concurrency semaphores, SSE event handling, conversation polling fallback, and direct gateway chat completion support.
  - Implemented `calculate_percentiles` (p50, p90, p95, p99) for latency and TTFT.
- **`services/app/scripts/run_scenario.py`**:
  - Created executable CLI tool for orchestrating pre-scrape -> replay -> post-scrape -> delta math -> report generation.
- **`Makefile`**:
  - Added repeatable targets: `replay-fanout`, `replay-multi-turn`, `replay-compare-strategies`.
- **`services/app/tests/`**:
  - `test_scenarios.py`: Validation of models, canned scenario files, and catalogue alignment.
  - `test_metrics_scraper.py`: Prometheus text parsing, delta math, reset detection, and mock scraping.
  - `test_replayer.py`: SSE stream handling, polling fallback, gateway direct execution, and percentile calculations.
  - `test_run_scenario_cli.py`: CLI execution and markdown report generation.

## Verification

- `source services/app/.venv/bin/activate && pytest services/app/tests` -> **235 passed**, 0 failures.
- `uv run --project services/app ruff check services/app` -> **All checks passed!**
- `uv run --project services/app black --check services/app` -> **87 files unchanged, clean formatting.**
- `make help` -> lists `replay-fanout`, `replay-multi-turn`, and `replay-compare-strategies`.
- `services/app/scripts/run_scenario.py --help` -> functional CLI flags.
