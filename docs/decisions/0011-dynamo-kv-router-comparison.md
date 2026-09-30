# ADR 0011 — Compare the custom router against Dynamo's KV-aware router (stretch, lab only)

## Status

Proposed. Nothing is deployed. Actually deploying Dynamo needs an explicit user decision.

## Context

#123 measures our own placement path (gateway policies `least_loaded` and
`prefix_then_load`, the latter an approximation of prefix locality built from the request's
`x-prefix-id`/`x-prefix-tokens` hints). It cannot see the engine's real KV state. NVIDIA
Dynamo ships a router that scores workers with engine-reported KV block events. Comparing
against it answers one question: how much does an engine-informed router improve over our
approximation on the same trace? This is comparison and stretch work (E6). It must not
replace understanding or measuring the custom path (E3 stays the headline).

## Decision (proposed)

1. Add a third arm, "Dynamo KV-aware", to the E3 evidence set. Protocol:
   [inference-e6-dynamo-comparison.md](../inference-e6-dynamo-comparison.md).
2. Dynamo replaces only the routing/frontend layer for that arm. Model, tokenizer, chat
   template, vLLM version, engine flags, worker count and GPU slices stay identical. The
   custom gateway is not modified.
3. Scope: the isolated Lambda/k3s lab of [ADR 0010](0010-transferable-lambda-inference-lab.md)
   only. No change to the AWS-only product boundary, no second hosting platform for the
   product, the FastAPI app still owns LLM calls and durable state. Dynamo is a measurement
   subject, not a product dependency.
4. Deployment (only after approval), documented here rather than committed as manifests so
   the manifests contract test (`infra/inference/tests/test_manifests_contract.py`) is not
   affected:
   - separate namespace (for example `dynamo-lab`), never the existing inference namespace's
     resources; the gateway and its workers are scaled down or left idle while the Dynamo
     arm runs (one arm at a time);
   - Dynamo platform/operator install per the upstream Kubernetes guide, or, simpler for a
     single node, a standalone `python -m dynamo.frontend --router-mode kv` plus two vLLM
     workers started with the same flags as ours;
   - two vLLM workers as separate HAMi slices on the one A100, same slice sizes as today;
   - if later committed, manifests would live under `infra/inference/dynamo/` and the
     contract test would be reviewed in that PR.
5. Parity is enforced, not assumed: `run_scenario.py --require-parity` and
   `python -m app.benchmarks.parity` reject missing/`unknown` model, tokenizer, template,
   engine flags, vLLM and Dynamo versions and KV block sizes, and any cross-arm difference.
   `manifest.scenario.sha256` is the arm-neutral source-workload hash; arm settings are in
   `manifest.execution`.
6. Harness accommodation (this PR): `run_scenario.py --router-label` records the arm in the
   manifest; non-gateway arms tolerate missing `x-place-decision` headers and reject
   gateway-only controls (`--policy-override`, `--admission-mode`).

## What research supports (docs, verified 2026-09-30)

- Router: cost = `overlap_score_weight * prefill_blocks + decode_blocks`, lowest cost wins;
  `router_temperature` > 0 adds softmax sampling. Router design page (v1.0.1):
  https://docs.nvidia.com/dynamo/v1.0.1/design-docs/component-design/router-design
- KV state: workers publish KV stored/removed events; the router keeps a radix-tree
  indexer, default transport NATS core/event plane with local indexer and gap detection,
  optional JetStream (`--durable-kv-events`, on frontend and workers), `--event-plane`
  (NATS or ZMQ), `--no-kv-events` for an approximate mode (same page).
- Standalone indexer: `dynamo-kv-indexer` consumes ZMQ KV events, exposes `/health`,
  `/metrics` (Prometheus), `/query` etc.; "bare vLLM and SGLang engines emit compatible
  events natively":
  https://docs.nvidia.com/dynamo/v1.0.0/additional-resources/standalone-indexer.md
- Frontend: OpenAI-compatible `/v1/chat/completions`; `python -m dynamo.frontend
  --router-mode kv --http-port 8000`; on Kubernetes `DYN_ROUTER_MODE=kv` on the frontend
  service; `DynamoGraphDeployment` CRD with vLLM "Aggregated + Router" template:
  https://docs.nvidia.com/dynamo/latest/components/router/router-guide ,
  https://docs.dynamo.nvidia.com/dynamo/recipes/kubernetes-templates/dgd/v-llm
- Metrics: frontend exposes `dynamo_frontend_*` at `/metrics` (port 8000); workers expose
  `dynamo_component_kvstats_*` (for example `..._gpu_prefix_cache_hit_rate`) on
  `DYN_SYSTEM_PORT` (8081 default) per the v0.9.0 metrics page
  https://docs.nvidia.com/dynamo/v-0-9-0/user-guides/observability-local/metrics
- Repo README reports release 1.0 and a `vllm-runtime:1.5.0` container tag; discovery on
  Kubernetes is native (no etcd/NATS needed), local dev can use `--discovery-backend file`:
  https://github.com/ai-dynamo/dynamo

## Not verified (must be checked before any deploy)

- Exact pinned Dynamo version and its vLLM version versus our vLLM version. The README
  summary shows inconsistent version numbers (1.0 vs a 1.5.0 runtime tag); pick one tag and
  record it in the manifest. Version drift in vLLM would break "same engine".
- Whether the current release still requires NATS/etcd for the KV event plane in k8s mode
  (sources disagree by version: NATS default event plane vs "not required on Kubernetes").
- Exact frontend metric names for routing decisions and overlap/cache-hit scores; only the
  prefixes above were seen. No documented per-request routing header/annotation was found.
- Whether the Dynamo vLLM worker wrapper accepts our exact engine flags unchanged.
- HAMi compatibility: no Dynamo documentation on fractional/HAMi GPU slices was found.
  Dynamo's docs assume whole-GPU resource requests; two workers on separate HAMi slices is
  an untested assumption, as is whether NIXL/UCX-related startup checks work on slices.
- That Qwen3-0.6B, our tokenizer/chat template and Dynamo's frontend preprocessing tokenize
  identically (block hashing needs identical token ids and block size).

## Risks

- Same-GPU contention: both arms share one physical A100; a leftover arm, DCGM or warm-up
  traffic skews results. Mitigation: one arm at a time, scale the other to zero, record
  idle GPU state.
- Version drift and engine parity: Dynamo's worker image may bundle a different vLLM.
  Parity is a precondition, not an assumption.
- Extra components (frontend, event plane, possibly NATS/etcd, operator) add latency and
  failure modes unrelated to routing quality; frontend overhead is part of what is
  measured and must be stated.
- Two workers only: differences between routers are small in a two-worker system; expect
  wide uncertainty and do not extrapolate.
- Scope creep: Dynamo also offers disaggregated serving, planner and KV offload; none of
  those are in this comparison.

## Alternatives

- Do nothing: keeps the story simple but leaves "how good is our approximation" unanswered.
- Reimplement engine-fed KV events in our gateway: larger, is exactly the custom path we are
  learning, and out of #123 scope.
- Compare on paper only: rejected for the claims, acceptable if approval is not given.

## Consequences

- Nothing changes in the product or in the gateway. The harness gains a router label.
- If approved, results are labelled "Dynamo vX on this lab, two workers, this trace" and
  never generalised. If not approved, this record and the protocol remain as plan.
