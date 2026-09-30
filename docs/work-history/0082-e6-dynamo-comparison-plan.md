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

- `uv run --project services/app pytest services/app/tests tests infra/inference/tests -q` — 542 passed (includes new `test_parity.py` and the three-arm hash test); black/ruff clean at CI scope.
- Not verified: anything about Dynamo itself. The research came from doc pages summarized by a small model, not full reads; Dynamo version to pin, NATS/etcd requirement, routing-decision metric names, per-request worker id, HAMi fractional-GPU compatibility, engine-flag compatibility, and tokenization/block-size parity are all open (see ADR 0011).

## PR / merge state

Draft PR (this change). Nothing deployed; deploying requires an explicit user decision.

## Review follow-up

- Scenario hash: `scenario.sha256` previously hashed the post-override config, so policy/admission arms (and Dynamo) could never match. It now hashes the source scenario before CLI overrides without `policy_override`/`admission_mode`; arm settings and `execution.sha256` are recorded separately (also fixes E3/E4 pairs).
- Parity: `unknown` could satisfy equality. New `app.benchmarks.parity` module, `--require-parity` (exit 2 before any request) and a manifest CLI reject missing/`unknown` fields (including vLLM/Dynamo versions and both KV block sizes) and cross-arm differences. New manifest fields `vllm_version`, `dynamo_version`, `kv_block_size`, `dynamo_kv_block_size`, `max_tokens`.
- Verification: see test_parity.py and the full suite below.

## Lessons

- Treat vendor-doc summaries as leads; re-read primary docs before pinning versions.
