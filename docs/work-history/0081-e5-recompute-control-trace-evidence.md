# Work history 0081 — #123 E5 recompute control, locality matrix scenarios, single-request trace, evidence index

## Goal

Everything for E5 and the evidence gate that does not need #133 or a cluster: controlled locality scenarios, the no-transfer recompute control, a single-request trace tool, and an evidence index that keeps unsupported claims out.

## Starting point

- No forced-worker control per turn, no locality-matrix scenarios, no way to join a request record with the gateway decision log, and no evidence index mapping the 13 instructor questions to artifacts.

## Decisions

- `force_worker` (`worker_a|worker_b`) per turn/conversation, sent as `x-force-worker`, honored only with `ALLOW_FORCED_PLACEMENT=1`. A forced turn counts only if `x-place-decision` equals the request and `x-placement-policy` is `forced`; otherwise `control_not_applied`, and an unhonored first forced response aborts the run. The first-response check is tracked per control. Manifest gains `treatment`, `force_worker_requested`, `force_worker_verified`.
- 13 generated scenarios (`e5_local_reuse_*`, `e5_recompute_control_*`, `e5_destination_hit_*` at about 1K/2K/4K/7K prefix tokens, plus `e5_local_eviction_4k`), produced by `benchmarks/e5_locality.py`; a test keeps the committed files equal to the generator and checks the 8192 context budget. Sizes are chars/4 estimates of synthetic padded prose; exact counts come from the manifest (`system_prefix.exact_tokens`). The eviction scenario cannot prove eviction happened and must be reported inconclusive without vLLM evidence.
- The real-transfer treatment is not implemented and documented as blocked on #133, to reuse the same four sizes. Nothing mentions a transfer backend.
- Trace tool (`benchmarks/trace.py`, `scripts/trace_request.py`, `make trace-request`) joins the request record, the gateway decision log, and labeled window-level evidence. A hop is shown only with a confirmed `stage:"hop"` record with `hop_result:"transferred"` (assumed field contract from #133); otherwise "not attempted (#133)".
- Gateway change is log-field only: `agent_step`, `received_at` on every line, `ts` on admit/place/reject lines. Behavior unchanged.
- `docs/inference-evidence-index.md` maps the 13 questions to artifacts with honest status and lists unsupported claims.

## Verification

- `uv run --project services/app pytest services/app/tests tests infra/inference/tests -q` — 551 passed; black/ruff clean at CI scope.
- Not verified: any run against a cluster; the `hop_result` log contract; 7K-token cases could approach the 8192 limit if tokenization is denser than chars/4.

## Gateway log-field gaps

- Guard is not timed separately (guard + quota + admission appear as one span); per-request vLLM wait/prefill/decode is not logged (window-level only); no hop log records until #133; tool execution is not in `requests.jsonl`; `pull-evidence.sh` does not pull the gateway log (use `kubectl logs deploy/inference-gateway > gateway.log`).

## PR / merge state

Draft PR (this change). `e5_local_eviction_4k.json` is about 300 KB because of filler prefixes.

## Lessons

- Keep generated scenarios reproducible from a generator plus a test, so committed files cannot drift.

## Review follow-up

- Hop confirmation: the trace marked a hop `confirmed` on `hop_result == "transferred"` alone. It now requires every field in `HOP_PROOF_FIELDS` (source/destination provenance, prefix identity, compatibility namespace, tokens and bytes > 0, duration, `transferred` result, `confirm_result: available`, observed destination reuse > 0). Otherwise the stage is `attempted_not_confirmed` with `missing_proof_fields`. Field names are an assumption until #133 lands. Tests cover a complete record and each missing field, a non-transferred result, and no record.
- Decode mislabel: `decode_ms_derived` (e2e minus TTFT) is renamed `post_first_token_ms` and described as client-observed, not vLLM decode; per-request engine prefill/decode timing is stated as unavailable (window-level only) in the trace note, guide and evidence index.

