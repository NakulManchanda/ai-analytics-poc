# Build from the repository root. CUDA image must be built on linux/amd64.
FROM vllm/vllm-openai:v0.11.0
RUN pip install --no-cache-dir lmcache==0.3.9 mooncake-transfer-engine==0.3.7.post2 \
    && pip check \
    && python -c "from mooncake.store import MooncakeDistributedStore; import lmcache"
COPY infra/inference/kv_transfer /opt/kv_transfer
ENV PYTHONPATH=/opt PYTHONHASHSEED=0 LMCACHE_TRACK_USAGE=false
