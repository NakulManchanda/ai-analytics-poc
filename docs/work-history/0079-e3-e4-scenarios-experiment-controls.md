# Work history 0079 — #123 Slice C: E3/E4 scenarios and test-only experiment controls

## Goal

Let the same traffic trace run under different routing policies and with admission on or off, without redeploying, so E3 and E4 can be compared fairly.

## Starting point

- Placement policy and admission were fixed per gateway deploy; the gateway replay path sent only a bare question (no shared prefix, no conversation history), so prefix affinity could not be exercised.

## Decisions

- Gateway experiment controls are honored only when `ALLOW_EXPERIMENT_CONTROLS=1` (default `0` in the manifest): `x-placement-policy-override` (invalid value -> 400 `invalid_experiment_control`, value never echoed), `x-admission-mode: off` (skips only `should_shed`; guard and tenant quota still run), `x-tenant-quota-mode: off` (gateway-only, never sent by the replayer).
  Applied overrides are echoed in `x-policy-override-applied` / `x-admission-mode` and logged; a forced worker still wins over a policy override. No new metric labels.
- Replayer gateway path: optional `system_prefix` (scenario or conversation) sends `[system prefix, prior completed turns, question]`, growing each turn; `x-prefix-id` defaults to a hash of the prefix (turn-level `prefix_id` overrides). Without `system_prefix` behavior is unchanged; `app_runs` is untouched.
- `ScenarioConfig.policy_override` / `admission_mode` and CLI flags `--policy-override` / `--admission-mode`; both recorded in the manifest and per-request records.
- Scenarios: `e3_routing_mixed` (10 conversations, 39 turns, one shared prefix, concurrency 8) and `e4_admission_overload` (22 conversations, 48 turns: `tenant_interactive`, `tenant_noisy`, `tenant_batch`; tight vs loose deadlines). Questions come from the query catalogue.
- Make targets: `replay-e3-least-loaded`, `replay-e3-prefix-then-load`, `replay-e4-admission-on`, `replay-e4-admission-off`. The testing guide documents gateway env (`ALLOW_EXPERIMENT_CONTROLS=1`, `TENANT_ALLOWLIST=tenant_interactive,tenant_noisy,tenant_batch`, admission thresholds).

## Verification

- `uv run --project services/app pytest services/app/tests tests -q` — 330 passed; `pytest infra/inference/tests -q` — 161 passed; black/ruff clean at CI scope.
- Not verified: any run against a live gateway/cluster; thresholds are uncalibrated. The E4 scenario file is ~62 KB because the long batch prefix is embedded per conversation.

## Review follow-up

- **Prefix belief truthfulness (HIGH).** A system-only `x-prefix-id` was recorded with the whole prompt's token estimate, inflating `estimated_reusable_tokens` and the 0.8 overlap test. Replayer now sends `x-prefix-tokens` (system-prefix region only, ~4 chars/token); the gateway clamps it to `[0, est_tokens]`, stores it as the reusable belief, and uses it as the overlap denominator (`PlacementRequest.prefix_tokens`). Absent or invalid header keeps the legacy whole-prompt behavior. E3 therefore measures shared system-prefix affinity only; history-aware prefix identity (system + prior completed turns, per `docs/prefix-contract.md`) is a possible follow-up, not measured here.
- **Ignored controls (MEDIUM).** Requested controls must be echoed by the gateway. First response missing/mismatching -> run aborts (exit 2, no evidence); later mismatches -> turn status `control_not_applied` (failed). Manifest: `policy_override_requested/_verified`, `admission_mode_requested/_verified`, `control_unverified_turns` (replaces the old `policy_override`/`admission_mode` keys).
- Verification: `pytest services/app/tests tests infra/inference/tests` — 513 passed; black/ruff clean.

## PR / merge state

Draft PR (this change). Next: Slice D real runs (E0/E1/E2), Slice E proofs.

## Lessons

- Experiment controls must be inert by default and never echo attacker-controlled values.
