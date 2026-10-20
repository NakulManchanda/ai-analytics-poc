# Evaluation foundations

Scope: the offline `tooling/evals/` slice of #171, plus real `capture.v1` ingest and the session report (#176). The live direct-Bedrock vs LiteLLM comparison is still a follow-up.

**Status: the baseline/candidate/regressed fixtures (traces, run manifests, recorded judge scores) are synthetic. `fixtures/captured/` holds one recorded real-shaped session produced by the real loop with a scripted provider (NOT live Bedrock). All 37 golden cases are `pending_human_review`.** Nothing here is a live model observation or a human-approved reference yet.

## Feedback loop

1. **Observe**: runs emit `capture.v1` JSONL (one record per model or tool call, joined by `run_id`).
2. **Triage**: failures and expensive or slow runs are clustered; interesting ones become *candidate*
   cases. Production/demo observations are never golden by themselves.
3. **Review**: a human writes the reference SQL, `evals refresh-reference` computes the expected facts
   on the pinned parquet, and the case lands in `datasets/golden/<version>/` through a PR. A model
   answer or model SQL is never ground truth.
4. **Grade**: deterministic graders (numeric facts, tool arguments, step budget, evidence present,
   limitation stated, no fabricated numbers) run on every PR over recorded fixtures. DeepEval judge
   metrics are replayed from recorded verdicts.
5. **Compare**: `evals report` diffs a candidate run against a baseline using the thresholds pinned in
   the run manifest (dataset version and hash, model alias, prompt version, judge model, thresholds).
   Regressions exit non-zero. Cost, tokens, latency and failure rate are reported next to quality.
6. **Decide and grow**: ship or reject the model/prompt change, then add reviewed cases for new
   failure clusters and bump the dataset version (never edit a released version silently).

## Dataset v1 (37 cases)

14 deterministic catalogue questions, 8 ReAct investigations, 5 multi-turn conversations, 6
unsupported/causal requests, 4 failure scenarios (tool validation error, timeout, empty result,
provider error). Every `review.status` is `pending_human_review` until the PR reviewer promotes it.
Facts use tolerances (counts 0.1%, averages 1%) and accept `23.8%` for `0.2376`.

## Judge metrics and their limits

Metrics: answer quality (GEval), tool correctness (DeepEval, deterministic), faithfulness, task
completion, step efficiency. Normal CI never calls a model: `RecordedJudge` replays stored scores.
Live scoring needs `uv sync --extra judge`, `EVALS_LIVE_JUDGE=1` and a judge model; it costs money and
is unverified against a real judge in this slice.

- Judges are probabilistic, drift across judge-model versions, and favour fluent or long answers.
  Treat scores as a screening signal, not ground truth; deterministic graders gate numeric claims.
- Compare runs only under the same judge model and version (stored in the manifest).
- Faithfulness here uses the reviewed reference facts as evidence because `capture.v1` stores row
  counts and query ids, not raw tool results. It does not detect a correct number that the run never
  retrieved; the `evidence_present` check covers that.
- Judge-vs-human calibration needs a hand-labeled set; not done yet. Recorded judge scores in
  `fixtures/` are synthetic.
- Small dataset: a one-case swing moves pass rate ~2.7 points. Thresholds are initial guesses.

## Captured traffic: candidate -> reviewed golden -> comparison (#176)

Real `capture.v1` JSONL has `run_id`/`turn_id`/`call_id`/`conversation_id` but no `case_id`/`turn_index`.

1. `evals candidates --traces captured.jsonl` (needs question text, i.e. a capture made with `CAPTURE_CONTENT=1`; metadata-only captures have no text to match and must be linked by a human-written matches file) lists each run with *suggested* golden turns (exact question match; may be several). A suggestion is not a label.
2. A human writes a matches file (`evals.matches.v1`: `run_id`, `case_id`, `turn_index`, `confirmed_by`). Validation rejects unknown cases, bad turn indexes, a missing `confirmed_by`, and duplicate runs or targets.
3. `resolve()` grades a matched run only if the golden case is `review.status == "reviewed"`. Runs that are unmatched, or matched to a still-pending case, stay *candidate only* and are not graded. `--provisional` grades against pending references and the report is then marked PROVISIONAL.
4. `evals session-report --traces ... --matches ... [--conversation-id ID] [--provisional]` writes the per-session report: answer correctness, tool behavior (actual vs expected), tokens, cost, latency. LIVE is derived only from the `execution_mode` recorded in the capture and requires every record to say `live`; anything else (scripted, unverified, missing) prints NOT LIVE. `--origin live` is only a cross-check and is refused when the capture is not recorded as live. The file must hold exactly one conversation, or select one with `--conversation-id`; otherwise the command errors. Unknown tokens/cost render `n/a` and totals are marked as lower bounds.

`build_turns()` no longer raises on unlinked records; it skips them (use `build_run_views()` for raw capture). Tool status `completed` (the app) and `ok` (synthetic) both count as success.

`fixtures/captured/taxi-session.jsonl` is a two-turn session (`multi-001`) recorded by `services/app/tests/capture_session_fixture.py`: real `OrchestrationLoop`, `LiteLLMClient`, model policy/pricing and capture recorder; scripted provider (httpx mock) and scripted MCP. Answers, token counts and latency are scripted, so correctness passing is by construction; it proves the pipeline, not model quality. `matches.json` there is fixture wiring by the agent, not a golden review. `report.md` is the PROVISIONAL report for it. Replace with a live Bedrock capture (same commands; it reports LIVE only if the capture was recorded with `CAPTURE_EXECUTION_MODE=live`, which is an operator assertion made at capture time) once a run is available.

## Running a live baseline

A live baseline runs the golden cases through the real app path (LiteLLM -> Bedrock -> MCP) and freezes the raw evidence so a later change can be compared against it. It measures the system as shipped; it does not change prompts, tools, models, or golden values. Results are **provisional** until the cases are human-reviewed.

### What a turn touches

```mermaid
sequenceDiagram
    autonumber
    participant D as Driver (POST /api/runs)
    participant W as web (nginx)
    participant A as app (FastAPI, OrchestrationLoop)
    participant S as DynamoDB (conversation + Bedrock allowance)
    participant R as Redis (RunEvents for SSE)
    participant L as LiteLLM proxy
    participant B as Bedrock (Nova Micro)
    participant P as Postgres (LiteLLM spend logs)
    participant M as MCP (DuckDB over Jan-2024 parquet)
    participant C as capture JSONL (bind mount)

    D->>W: POST /api/runs {prompt, conversation_id?}
    W->>A: proxy to app
    A->>S: store question, load conversation
    A-->>D: 202 {conversation_id, run_id, events_url}
    D->>W: GET /api/runs/{run_id}/events (SSE)
    A-)R: publish RunEvents at each step (SSE reads these)
    A->>A: query catalogue lookup (deterministic)
    alt catalogue hit (tool_source=catalogue)
        Note over A,L: no planner call; LiteLLM sees nothing for this step
    else miss (tool_source=model)
        A->>A: model policy (entitlement, per-call cap, per-run reservation)
        A->>S: monthly Bedrock allowance reservation
        A->>L: planner call (alias taxi-planner, legacy tools only)
        L->>B: Converse
        L->>P: spend row (+ bodies if LITELLM_CAPTURE=1)
        L-->>A: proposed tool call
    end
    A->>M: tool call (aggregate / compare / query ...)
    M-->>A: rows (+ truncated flag)
    A->>A: ContextReducer, validation, grounding check
    A->>A: model policy + allowance reservation (as above)
    A->>L: answer call (alias taxi-answer)
    L->>B: Converse
    L->>P: spend row
    L-->>A: answer
    A->>S: persist answer
    A->>C: model_call + tool_call records (capture.v1)
    R-->>D: SSE events until run.completed
```

| Component | Kind | Role in a baseline | Holds |
|---|---|---|---|
| Driver | local script | Submits each turn, waits for a terminal event, records IDs. No retries. | `results.jsonl`, `matches.json`, event text |
| `web` | infra (nginx) | Entry point for `/api/*`. | nothing |
| `app` | service | Catalogue, planner/answer calls, MCP calls, reduction, validation, capture. Owns every LLM call and loop budget. | capture JSONL |
| DynamoDB | AWS | Durable conversation state and the shared monthly Bedrock allowance. | conversations, allowance |
| Redis | infra | Transient RunEvent fan-out for SSE only; never durable. | nothing durable |
| LiteLLM + Postgres | service + infra | Model alias routing, budgets, per-call spend rows, optional request/response bodies. | `LiteLLM_SpendLogs` |
| Bedrock | AWS | The model. | AWS billing |
| `mcp` | service | Governed read-only analytics tools over the pinned parquet. Never calls an LLM. | nothing |
| `worker` | service | Async job consumer; not on the `/api/runs` path. | nothing |

Catalogue-served turns make no planner call, so LiteLLM holds fewer rows than there are turns; the app capture is the complete per-run record.

### Procedure

1. **Freeze inputs.** Record the git commit, dataset hash (`datasets/golden/<version>/cases.jsonl`), LiteLLM config hash, container image IDs, model aliases, and prompt template versions. Do not edit code, prompts, tools, models, or golden values during the baseline or the later AFTER run.
2. **Bring the stack up** from the checkout under test with app capture on (`CAPTURE_DIR`, `CAPTURE_EXECUTION_MODE=live`, `CAPTURE_CONTENT=1`; see [capture](capture.md)) and, if proxy bodies are wanted, `LITELLM_CAPTURE=1` (see [LiteLLM proxy](litellm-proxy.md)). Check `make litellm-ready` and `/api/status`. Confirm `LLM_PROVIDER=litellm`.
3. **Smoke one run** (`make litellm-smoke`) and confirm the capture record carries `prompt_template_id`, version, hash, and `execution_mode=live`.
4. **Estimate and agree spend** before the full run, and watch the monthly allowance (`GLOBAL_BEDROCK_MONTHLY_LIMIT_USD`). Stop if provider errors become systematic or the environment changes.
5. **Drive every case once.** One new conversation per case; reuse the returned `conversation_id` for later turns of the same case. For each turn: `POST /api/runs`, read `GET /api/runs/{run_id}/events` to a terminal event, and record `case_id`, `turn_index`, `conversation_id`, `run_id`, and the terminal event. Do not retry or rerun cases to improve scores; keep every failure.
6. **Snapshot the evidence.** Copy the capture file, make it read-only, and store its sha256. Keep the event streams.
7. **Write the matches file** (`evals.matches.v1`) from the driver's `results.jsonl`, then grade with `evals session-report --provisional --conversation-id <id>`, one conversation at a time.
8. **Analyse each turn.** Read the answer against the tool rows (events: `context.reduced` -> `working_context.recent_messages`), not just the grader output.

### Identifiers and sources of truth

There is no single baseline ID: the baseline label lives only in the driver's files. `results.jsonl` is the index.

| ID | In | Notes |
|---|---|---|
| `conversation_id` | driver, capture, events, LiteLLM as `session_id` | one per case; the join key to LiteLLM |
| `run_id` | driver, capture, events | one per turn; not in LiteLLM |
| `case_id`, `turn_index` | driver, matches file | not in capture or LiteLLM |
| capture `call_id` vs LiteLLM `request_id` | capture / LiteLLM | not the same value; pair by `session_id`, alias, order |

| Need | Source |
|---|---|
| Billed cost per call | LiteLLM `spend` (capture `cost_usd` agrees); AWS billing is final |
| Request/response bodies, proposed tool calls | LiteLLM `/spend/logs` (only with `LITELLM_CAPTURE=1`) |
| Executed tool arguments, status, latency, answer, prompt version | app capture JSONL |
| Tool result rows | run events (`context.reduced`) |

`/spend/logs` returns raw rows only when called without `start_date`/`end_date`; with them it returns daily aggregates. Use `/spend/logs/ui` for paged lists and `?request_id=` for one full row. The `cost_usd` inside run events is an app-side estimate and has been about 100x the capture/proxy cost; do not use it.

### Reading the results honestly

- `run.completed` means the workflow finished, not that the answer is right. Report execution, tool correctness, and answer correctness separately.
- Check `tool_source` in the `step.llm_proposal` event: `catalogue` turns test the deterministic catalogue; `model` turns test the planner, which today is offered only the legacy tools and always emits a tool call.
- The deterministic graders can pass a wrong answer (numbers present but mislabelled or incomplete) and fail an acceptable one. Treat the pass rate as a legacy score until the graders check label association, completeness, and required arguments.
- Mark cases with incompatible reference semantics (for example `multi-001`, `fail-003`) diagnostic, with the reason, instead of silently dropping them. Genuine model failures stay in the metrics.

## Known gaps

- A first live provisional baseline exists only as local, uncommitted evidence; the committed session report is local-scripted. Its legacy grader score is not a trusted quality metric.
- `multi-001` carries the `diagnostic` tag: it is graded and listed in a separate "Diagnostic cases" section of delta reports, but excluded from the aggregate pass rate, tool-correctness mean, per-category rates and regression gates. It is retained for query-resolution (#110) diagnosis until the filter contract between `query_taxi_data` and the governed tools is reconciled. Its turn 2 asserts the entities (nested subset match on `baseline_filters`/`comparison_filters`: JFK Airport vs Midtown Center); subset matching recurses into nested objects, `exact` still requires identical keys.
- Golden cases are all pending human review, so strict mode grades nothing; reviewing `multi-001` is the next human step.
- Matching is manual by design; there is no automatic promotion of captured runs to golden cases.

Fixtures are synthetic, so no real model quality or cost decision is supported yet. The `app_runs` direct vs LiteLLM rerun, quality-qualified goodput joined with the benchmark SLO
goodput (here: pass rate only), failure clustering and judge calibration are follow-ups.
