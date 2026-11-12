# Inference Experiment Evidence

This index connects **GPU and gateway experiments** to their measured outcomes and supporting artifacts. It distinguishes observed behavior from implemented mechanisms that have not yet been demonstrated live.

## What we measure

| Experiment | Question | Primary evidence |
| --- | --- | --- |
| **Capacity** | What limits throughput: scheduler slots, KV memory, queue, or compute? | Concurrency sweep, worker metrics, GPU utilization |
| **Warmup & prefix reuse** | Does a warm worker or cached prompt improve first-token latency? | TTFT, prefix-cache hits, worker startup observations |
| **Placement** | Does prefix-aware routing outperform least-loaded routing? | Worker decisions, per-turn TTFT, tail latency |
| **Admission** | Does controlled shedding protect latency and fairness under overload? | Admit/shed reasons, queue wait, successful throughput |
| **KV mobility** | Is transferring KV between workers better than recomputing? | Source/destination provenance, bytes and tokens transferred, destination reuse |

## Evidence and limits

- **Observed:** In the small-model A100 experiments, scheduler slots constrained capacity before KV memory. Prefix reuse improved some first-token timings; prefix-aware placement was not consistently better at p95. Admission results were neutral under the measured load.
- **Implemented but not yet proven live:** End-to-end KV transfer with confirmed destination consumption, eviction behavior, and crossover gains compared with recomputation.
- **Measurement boundaries:** Gateway decisions, per-request latency, and Prometheus window-level aggregates are different kinds of evidence. A routing decision or lower latency alone does **not** demonstrate KV transfer or cache reuse.
- **Environment:** Tests used two isolated workers on one physical A100; some client-side latency includes SSH tunnel overhead. Results should not be generalized to multi-GPU deployments without further measurements.

## Artifacts

- [Experiment methodology and scenarios](inference-experiments-reference.md)
- [Committed measurement summaries](evidence/metrics/)
- [Charts](evidence/plots/)
- [Executed notebook](evidence/notebook/)
- [Reproduction playbook](inference-run-playbook.md)

For a result to be considered verified, retain its workload configuration, metrics window, request-level observations, and relevant worker evidence.
