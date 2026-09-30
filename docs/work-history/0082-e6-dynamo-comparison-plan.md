# Work history 0082 — #123 E6: Dynamo KV-aware router comparison plan and harness accommodation

## Goal

Prepare the optional E6 comparison (our `prefix_then_load` vs an engine-informed router) without deploying anything, and make the harness label and tolerate a non-gateway arm.

## Starting point

- The harness assumed our gateway (decision headers, policy/admission controls). No decision record existed for a Dynamo comparison.

## Decisions

- ADR 0011 (status Proposed) records purpose, scope inside the isolated lab boundary (ADR 0010), what Dynamo replaces (routing/frontend layer only; workers, model and flags identical), risks, alternatives, cited sources, and an explicit not-verified list. No manifests were added; deployment is documented only.
- `docs/inference-e6-dynamo-comparison.md` defines three arms (gateway `least_loaded`, gateway `prefix_then_load`, Dynamo KV-aware), identical inputs (E3 headline and large-prefix scenarios by hash, SLOs, revisions, flags, topology), isolation (one arm at a time, cold start, alternating order), per-request vs window-level evidence, routing distribution without our headers, and interpretation guardrails.
- `run_scenario.py` gains `--router-label` (default `gateway`); a non-gateway label with `--policy-override` or `--admission-mode` exits 2 before any request. Manifest records `router_label`, `gateway_decision_headers_expected`, `turns_with_decision_headers`.
- No per-worker metrics URL option: the scraper reads one URL per run; the protocol documents per-worker before/after diffs or a Prometheus range query over the manifest window.

## Verification

- `uv run --project services/app pytest services/app/tests tests infra/inference/tests -q` — 532 passed; black/ruff clean at CI scope.
- Not verified: anything about Dynamo itself. The research came from doc pages summarized by a small model, not full reads; Dynamo version to pin, NATS/etcd requirement, routing-decision metric names, per-request worker id, HAMi fractional-GPU compatibility, engine-flag compatibility, and tokenization/block-size parity are all open (see ADR 0011).

## PR / merge state

Draft PR (this change). Nothing deployed; deploying requires an explicit user decision.

## Lessons

- Treat vendor-doc summaries as leads; re-read primary docs before pinning versions.
