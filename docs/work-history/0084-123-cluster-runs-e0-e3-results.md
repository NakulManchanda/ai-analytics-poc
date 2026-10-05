# Work history 0084 — #123 cluster runs E0-E3: results, follow-up fixes, playbook prerequisites

## Goal

Run the #123 experiments E0 (capacity), E1 (cold vs declared-warm), E2 (prefix reuse) and E3 (routing) on the Lambda A100 / k3s cluster, keep the fixes found along the way, and record what the evidence does and does not show. Operator ran every cluster command; analysis and write-ups were done locally from the pulled evidence. E4, E5 and the fresh-instance rehearsal were not run.

## Starting point

Work history 0083 left the cluster deployed and E1 done. The playbook's E0 section lacked the tenant-quota step, and its E2-E4 sections said nothing about experiment controls or quota.

## Decisions

- **Prerequisites are explicit per experiment.** E2 and E3 need `make inference-controls-on` and `TENANT_MAX_CONCURRENCY=64` (they replay at concurrency 8; the default bucket of 4 gives 429s). E4 needs the default quota restored. The first E2 attempt was invalid for exactly these reasons (controls off, 429s) and was kept, not deleted. The playbook now says so in the E0, E2, E3 and E4 sections and uses the manifest env files (`source ...env` once per experiment, so both arms share a run folder).
- **E1 run script:** `run-e1.sh` waits 30 s before the Prometheus window ends; scrapes are 15 s and the TTFT query uses a 1 m rate, so without it the warm run was missing from the export.
- **Dashboard:** the mean series on the prefill/decode panels no longer uses `clamp_min`, so idle periods show NaN instead of a misleading 0.
- **E3 arm procedure:** both workers restarted (and the warm-up run) before EACH arm so both start from the same cache state; the env is sourced once.

## Verification

All numbers below are client-side (through the SSH tunnel) unless stated; Prometheus numbers are window-level, not per-request.

- **E0:** the first limiter is the 8-slot scheduler cap (`--max-num-seqs 8`), then the batched-token limit at 8,192-token contexts. KV is never the limiter (46% peak in the direct capacity run, 3% in the gateway sweep). Gateway sweep throughput peaks around concurrency 12 (5.92 req/s, 757 tok/s); no 429s with quota 64. Goodput is 0: the 100 ms TTFT SLO cannot be met through the tunnel.
- **E1:** engine-side first request after a restart is slightly slower, none after a 300 s idle gap; the tunnel hides it. Restart recovery about 95 s.
- **E2:** `e2_prefix_reuse` comparable and proven. Turn-1 TTFT p50 cold 2,132 ms vs reused 681 ms (about 3x); turns 2-5 no reliable difference; overall p95 2,266 -> 756 ms. Window hit rate 81.8% vs 79.3%, not discriminating.
- **E3:** `e3_compare` comparable and proven (only `policy_override` differs, both verified). `least_loaded` vs `prefix_then_load`: TTFT p50 561 vs 591 ms, p95 1,796 vs 2,702 ms, e2e p50 1,506 vs 1,188 ms, throughput 3.49 vs 3.85 req/s. The p95 gap is turn 1 (9 of 10 first turns went to worker A and queued; p50 2,702 vs 1,795 ms); turns 3-4 are faster under `prefix_then_load`; `local_reuse` 25 vs 2 of 39. A trade-off from one run per arm, not a clear win.
- **Memory proof** (E0 range): no flags, no preemptions; HBM flat except one +440 MiB step that coincides in time with the KV peak (cause inferred, not verified); KV never near the limit.
- **Notebook:** `experiments/123_evidence.ipynb` executed headless over E0-E3 plus the memory proof (E4/E5 skipped: no run directories); the committed notebook still has `RUNS` empty.
- Local: `uv run --project services/app pytest infra/inference/tests/test_dashboards_contract.py tests/inference/test_cluster_bundle_contract.py -q` — 23 passed (dashboard change).
- Evidence for E0, E1, E2 and E3 (arms, Prometheus ranges, cluster snapshots) pulled locally before the instance was terminated; local backup tarball made outside the repo. Cluster teardown and instance termination done by the operator.
- Not verified: CI on the pushed commits (pushed directly to `main`, bypassing the required "Python services" check), `tmux-session.sh`, `inference-fresh-up`, the startupProbe under a confirmed slow start, the dashboard `mean` series in Grafana.

## PR / merge state

No PR. Committed directly to `main` by request (the changes touch more than the 1-2 files of the direct-to-main exception in AGENTS.md); the run-fix commit is `1cd10e5`. Nothing here deploys or applies infrastructure.

## Open items

- The declared warm-up wrote empty `warmup_summary.json` (`{}`) in the E3 run (both arms), so the warm state is unproven; cause not investigated.
- E3: from turn 2 all follow-up requests were placed with reason `prefix_overlap_low`, and conversations still moved between workers; why overlap is judged low was not checked. The large-prefix variant (`replay-e3-large-prefix-*`) was not run.
- Worker A took 135 s to restart in E2 and E3 (E1: 95 s); pod events not checked.
- Result quality limits: replay through the SSH tunnel (TTFT floor of 300+ ms), short runs of 10-11 s against a 15 s scrape, one run per arm in fixed order, workers on two slices of one GPU. Better configuration: replay inside the cluster, longer prefixes, longer runs with faster scrapes, repeated arms in ABBA order, arrival-rate load.
- Prometheus series appear twice (jobs `kubernetes-pods` and `inference-workers`); filter by `job`.
- E4, E5 and the fresh-instance rehearsal are still to do and need a new instance.

## Lessons

- Experiments that look comparable still depend on gateway state (controls, quota); put the prerequisite in the playbook section, not in memory.
- Check what a result is measured against before attributing it: the 46% KV peak in the E0 range came from the capacity run, not the gateway sweep.
- A pull of cluster snapshots is only possible while the instance is up; do it before teardown, and back up gitignored evidence (`metrics/inference/` is not in git).
