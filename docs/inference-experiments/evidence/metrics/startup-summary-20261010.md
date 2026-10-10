# Startup snapshot summary (fresh A100 instance, 2026-10-10)

Source: files `01`-`05` in this folder (tunnel output, `worker_warm` query, gateway `/metrics`, worker A vLLM `/metrics`, `make inference-verify-workers`).
Read from the saved files only; the cluster was not re-queried. Instance `[instance-redacted]`, NVIDIA A100-SXM4-40GB.

| Check | Result |
|---|---|
| `worker_warm` | `1` for `worker_a` and `worker_b` (Prometheus via gateway service and via pod scrape agree) |
| `worker_health` | both `healthy`; `unhealthy` and `stale` are `0` |
| `worker_snapshot_age_seconds` | about 0.7 s on both (scrapes are fresh) |
| `warm_probe_total{result="ok"}` | 1 per worker (the warm gate's probe request passed, not only scrapes) |
| `worker_ramp_cap` | `0` on both (no active ramp) |
| `stale_snapshot_fallback_total` | `0` |
| `orch_replica_queue_depth` | `0` for interactive and batch on both workers |
| vLLM running / waiting / preemptions / KV usage | all `0` (idle) |
| vLLM cache config | prefix caching on, block size 16, `gpu_memory_utilization` 0.45, `cache_dtype` auto |
| `num_gpu_blocks` (worker A) | 4,546 blocks = 72,736 tokens (4,546 x 16) |
| `make inference-verify-workers` | exit OK: `args identical across workers: yes`, `engine settings identical across workers: yes` |
| Worker args (both) | vLLM v0.11.0, Qwen/Qwen3-0.6B (revision `c1899de...`), bfloat16, `--max-model-len 8192`, `--max-num-seqs 8`, `--max-num-batched-tokens 2048`, `--block-size 16`, prefix caching, hermes tool parser, `--gpu-memory-utilization 0.45` |
| Startup log (both) | `chunked_prefill_enabled=True`, `kv_cache_dtype=auto`, `max_seq_len=8192`, tokenizer revision equals model revision |

## Questions to check later (capacity maths)

Not answered yet; each needs a number from the startup log, `config.json`, or the next run. Do not edit the D2a/D2c write-ups until these are settled.

1. **Pool size changed.** Today 4,546 blocks (72,736 tokens). The E0 write-up used 71,200 tokens (about 4,450 blocks), a
   difference of roughly 96 blocks (about 2%). Is the cause the new `--max-num-batched-tokens 2048` (less activation memory,
   more left for KV)? Confirm from the vLLM startup log ("GPU KV cache size", "Maximum concurrency") on both workers,
   and check worker B has the same block count.
2. **Does the "KV cannot bind" argument still hold?** 8 slots x 8,192 = 65,536 tokens, still below 72,736. Margin is now
   about 7.2k tokens (about 10%) instead of about 5.7k. Restate it with the new figure.
3. **KV bytes per token.** For Qwen3-0.6B I expect 2 x layers x KV heads x head_dim x 2 bytes (fp16). Verify layers, KV
   heads and head_dim from the model `config.json` (do not rely on memory), then check that
   `num_gpu_blocks x 16 x bytes_per_token` matches the pool bytes the log reports.
4. **Where does the rest of the 20 GiB slice go?** `gpu_memory_utilization` 0.45 on a 40 GiB card; weights, activations
   and CUDA graphs vs KV. The earlier note said about 22 GiB of HBM stays unused; recompute it with the new pool and
   the DCGM `DCGM_FI_DEV_FB_USED` reading at idle.
5. **Capacity at the app's length.** Re-derive max concurrent sequences at max_len (8,192 -> 8.88 with the new pool,
   still capped to 8 by `--max-num-seqs`) and at the measured app lengths (p50 about 541 tokens, max about 984 from E3
   `tokens_in`+`tokens_out`). Recompute from the final run's `requests.jsonl`, not from E3.
6. **ANSWERED (05): flags match.** Both workers run `--max-num-seqs 8` and `--max-num-batched-tokens 2048`, identical args and
   engine settings. Remaining: the gateway test ties `MAX_DECODE_SLOTS`/`WORKER_MAX_INFLIGHT` to `--max-num-seqs` only,
   not to the batched-token flag.
7. **Does chunked prefill actually engage at 2048?** (Startup log shows `chunked_prefill_enabled=True`; the open part is
   whether a real long prompt gets split.) The E0 limiter note said the 8,192 batched-token limit was second.
   With 2048, a prompt over 2,048 tokens must be split. Confirm from a long-prompt request (vLLM prefill chunks or
   TTFT shape) before writing the Part 5 answer on chunked prefill.
8. **Unused-HBM and replica claim.** Two 50/50 HAMi slices each use 0.45 of their share. Is the per-slice utilisation
   fraction relative to the slice or to the whole card? This changes the "weights duplicated per replica" and "a third
   replica would fit" statements.
