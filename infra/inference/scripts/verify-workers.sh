#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/lib.sh"

if [[ "${1:-}" == "--help" ]]; then
  echo "Usage: verify-workers.sh"
  echo "Read-only: prints each worker deployment's image and args, whether A and B match, and the engine"
  echo "settings (vLLM version, revisions, block size, dtypes, chunked prefill, prefix caching) from each worker's startup log."
  echo "Exits non-zero when the args or engine settings differ, or the settings cannot be verified."
  echo "Compare with infra/inference/experiments/manifest/common.env before recording a run."
  exit 0
fi

load_local_env
require_connection
: "${INFERENCE_NAMESPACE:=inference-lab}"

# Remote commands go over stdin (quoted heredoc) so no quoting has to survive the ssh command line.
ssh_cmd "NS='$INFERENCE_NAMESPACE' KUBECONFIG=/etc/rancher/k3s/k3s.yaml bash -s" <<'REMOTE'
set -eu
echo "== Worker deployments (image, args) =="
kubectl -n "$NS" get deploy inference-worker-a inference-worker-b \
  -o custom-columns='NAME:.metadata.name,IMAGE:.spec.template.spec.containers[0].image,ARGS:.spec.template.spec.containers[0].args'
A=$(kubectl -n "$NS" get deploy inference-worker-a -o jsonpath='{.spec.template.spec.containers[0].args}')
B=$(kubectl -n "$NS" get deploy inference-worker-b -o jsonpath='{.spec.template.spec.containers[0].args}')
rc=0
if [ "$A" = "$B" ]; then echo "args identical across workers: yes"; else echo "args identical across workers: NO"; rc=1; fi
settings_a=""
settings_b=""
for w in a b; do
  echo "== worker-$w engine settings from startup log =="
  s=$(kubectl -n "$NS" logs "deploy/inference-worker-$w" \
    | grep -E 'non-default args|Initializing a V1 LLM engine' \
    | grep -o -E "engine \(v[0-9.]+\)|'revision': '[0-9a-f]+'|'block_size': [0-9]+|tokenizer_revision=[0-9a-f]+|kv_cache_dtype=[a-z0-9]+|max_seq_len=[0-9]+|chunked_prefill_enabled=[A-Za-z]+|enable_prefix_caching=[A-Za-z]+|dtype=torch\.[a-z0-9]+" \
    | sort -u || true)
  if [ -z "$s" ]; then echo "(no matching startup lines in the current log)"; else echo "$s"; fi
  if [ "$w" = a ]; then settings_a="$s"; else settings_b="$s"; fi
done
if [ -z "$settings_a" ] || [ -z "$settings_b" ]; then
  echo "engine settings identical across workers: UNVERIFIED (startup lines missing)"; rc=1
elif [ "$settings_a" = "$settings_b" ]; then
  echo "engine settings identical across workers: yes"
else
  echo "engine settings identical across workers: NO"; rc=1
fi
exit $rc
REMOTE
