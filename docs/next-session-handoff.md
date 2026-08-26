# Next session handoff

Last updated: 2026-08-25 (America/Toronto)

## Start here

The next implementation slice is one deliberately narrow PR that closes GitHub issues
[#58](https://github.com/NakulManchanda/ai-analytics-poc/issues/58) and
[#59](https://github.com/NakulManchanda/ai-analytics-poc/issues/59):

1. Add a governed, parameterized MCP tool named `average_trip_metrics`.
2. Fix the unreadable sample-question chip text color.

Do not include Redis issue #57, the other v1.1 follow-ups, v2 streaming, or unrelated cleanup
in this PR.

## Repository and deployment baseline

- Remote `main`: `2febb2166281e408af4dea9922b343394d4dd388`, merge commit for PR #56.
- Existing `v1` tag: annotated tag object `9f1eef8`, pointing to `fa5fe2f`.
- No `v1.1-foundation-truthful-state` tag was observed at this handoff.
- Last verified deployed application image/source checkpoint: `60373f3`, ECS task definition
  `ai-app:5`. PR #56 added integration verification and documentation after that runtime build.
- DynamoDB restart/reload recovery was verified for one conversation, four messages, two runs,
  and their steps.
- Redis is intentionally unconfigured in the deployed stack. Durable SSE reconstruction works,
  while the separate localhost-fallback cleanup remains issue #57. Do not provision new AWS
  infrastructure in this slice.

## Failure evidence for issue #58

The public sample prompt is:

```text
Compare average trip distance and fare amount across major pickup boroughs
```

The deployed API returned HTTP 422. DynamoDB run `run_a299b56584de4b4e` recorded:

- run status `failed`;
- failure code `tool_validation_error`;
- completed `llm_proposal` step with an empty tool name;
- failed `validation_error` step with `arguments: None`;
- no MCP/DuckDB execution.

The existing governed query surface only contains `top_pickup_zones`,
`trip_volume_by_hour`, and `average_distance_by_weekday`. This is an isolated analytics
capability gap, not a DynamoDB or Redis failure.

## Approved tool direction

Use the generic tool name `average_trip_metrics`, not
`average_trip_metrics_by_pickup_borough`.

The bounded initial contract is:

- optional governed `region_name` input;
- omitted region compares valid major pickup regions;
- supplied region filters to that named pickup region;
- output includes region name, trip count, average trip distance, and average fare amount;
- regions come from the pinned taxi-zone lookup;
- default comparison excludes sentinel/non-borough values such as `Unknown`, `N/A`, and `EWR`;
- unknown or malformed regions receive a stable, truthful, non-retryable validation response;
- no arbitrary SQL, paths, column selection, or unbounded grouping crosses the MCP boundary;
- existing governed analyses remain supported.

Keep the MCP server LLM-free. The application continues to own model calls, proposal validation,
loop budgets, and durable state.

## UI contrast evidence for issue #59

In `web/src/styles.css`, `.sample-chip` resolves `--color-surface-muted` to dark navy, while
`--color-text` is undefined and falls back to another dark color. The hover state must also keep
a readable foreground/background pair. Fix default, hover, and keyboard-focus contrast without
redesigning the page or changing click-to-populate behavior. Verify WCAG AA contrast and capture
browser evidence.

## Execution shape

```text
remote main (2febb216)
        |
        +-- .worktrees/average-trip-metrics
                branch: codex/average-trip-metrics
                issues: #58 + #59
                PR: one narrow PR linking both issues
```

Likely shared/backend files are listed in issue #58. The UI change should remain confined to
`web/src/styles.css` and focused verification. Start with failing tests for the tool contract and
the exact public prompt, then implement the dataset, MCP, application proposal/validation, and
answer-formatting path. Run focused tests, relevant full service suites, the frontend production
build, and GitHub Actions. Record architecture impact, exact commands, results, limitations, and
the repository-required work-history entry.

AWS checks are not needed on every local commit. After merge and a meaningful deploy checkpoint,
build immutable image tags from the merge commit, deploy intentionally, then run public smoke and
AWS/DynamoDB verification. Deployment is separate from merging unless an explicit workflow does
both.

## Separate open work

- #57: remove implicit localhost Redis behavior when Redis is unconfigured; no new infrastructure.
- #54 and #55: v1.1 UI/telemetry follow-ups.
- #44: v1.1 roadmap tracking.

Do not silently expand the #58/#59 PR to cover these issues.

## Suggested prompt for a new Codex task

```text
Read AGENTS.md, docs/next-session-handoff.md, issues #58 and #59, and the required project
requirements/plans. Implement #58 and #59 together in the single approved next PR. Work only in
the existing `.worktrees/average-trip-metrics` worktree on `codex/average-trip-metrics`, and use
TDD. The MCP tool must be named `average_trip_metrics` and accept an optional governed
`region_name`; do not add arbitrary SQL or start v2 streaming. Also fix sample-question chip
contrast. Link both issues, keep the PR narrow, run all relevant local checks and GitHub CI,
perform independent review, and report deployment as a separate post-merge action.
```
