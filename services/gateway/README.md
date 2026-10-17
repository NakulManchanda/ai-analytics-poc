# services/gateway

The inference gateway: a thin FastAPI service (guard -> tenant quota -> admission -> placement ->
per-worker queue -> hop decision -> worker proxy, plus overflow). It never loads a model and makes
no LLM calls of its own. Endpoints: `/serve`, `/v1/chat/completions`, `/tokenize`, `/metrics`, `/health`.

## Ownership
- **Code (this directory):** owned as a service. Unit and contract tests live in
  `infra/inference/tests/test_gateway_*.py` and `tests/inference/`; they import `services.gateway`.
- **Deployment (`infra/inference/`):** Kubernetes manifests (`k8s/gateway/gateway.yaml`,
  `k8s/services/gateway.yaml`), GPU worker manifests, KV-transfer and observability stay there.

## Deployment entrypoint
The pod runs `uvicorn main:app --app-dir /app` from the `inference-gateway-code` ConfigMap, which is
built from the remote directory `~/ai-analytics-inference/gateway`. `infra/inference/scripts/sync.sh`
and `gateway-restart.sh` copy this directory to that remote path (`GATEWAY_ROOT` in `lib.sh`), so the
remote layout and manifests are unchanged. Because the modules load as flat files in the pod, the
`try: from .x import ... / except ImportError: from x import ...` fallbacks must stay until a
packaged image replaces the ConfigMap.
