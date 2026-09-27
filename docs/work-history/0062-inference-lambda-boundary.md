# Work history 0062 — Inference Lambda deployment boundary

## Goal

Refine issue #120 and the inference plan with a durable decision for what stays local, what transfers to Lambda, how the services connect, and how KV capacity will be calculated and measured.

## Starting point

- `docs/inference-project-plan.md` described the target inference components but used a conceptual folder tree without a transfer boundary.
- Issue #120 required two real vLLM workers and capacity evidence but did not capture the agreed SSH, provider, artifact, or class9b reuse boundaries.
- The existing `LLMClient` already separated application orchestration from the Bedrock implementation.

## Decisions

- Keep the product, MCP, web application, existing observability, and canonical metrics local.
- Transfer only a self-contained `infra/inference/` bundle to Lambda.
- Run the cluster gateway, vLLM workers, cluster observability, and controlled traffic generator on Lambda.
- Use SSH forwarding for the initial lab; do not expose raw worker endpoints publicly.
- Preserve `LLMClient`, add one OpenAI-compatible adapter in #121, and defer LiteLLM Proxy.
- Plan Superlinked/SIE as the single eligible overload destination while retaining Bedrock as an explicitly selectable provider.
- Start capacity work from the analytical KV formula, then reconcile it with inside-pod memory, vLLM startup capacity, live metrics, and measured taxi p50/p95 contexts.
- Reuse class9b k3s/HAMi/tunnel mechanics, but do not treat its capability split or metadata-only Mooncake mock as proof of same-model routing or real KV transfer.

## Files

- `docs/decisions/0010-transferable-lambda-inference-lab.md`
- `docs/decisions/README.md`
- `docs/inference-project-plan.md`
- `docs/work-history/0062-inference-lambda-boundary.md`

## Verification

- `make check-bootstrap` — passed.
- `git diff --check` — passed with no whitespace errors.
- Required relative document targets were checked with `test -f` — passed.
- Changed committed docs were scanned for placeholders, developer-specific absolute paths, private-key markers, AWS access-key patterns, and secret-key assignments — no matches.
- `gh issue view 120` assertions confirmed the refined body contains the isolated-lab boundary, KV formula, `infra/inference/` transfer contract, and `NakulManchanda` assignment.
- No runtime tests were run because this change alters documentation and issue metadata only.

## PR and merge state

- Branch: `docs/120-inference-cluster-design`
- Worktree: `.worktrees/120-inference-cluster-design`
- Pull request: pending at documentation verification
- Merge: pending

## Lessons

- Source location and execution location must be documented separately: all source is local and versioned, while only one subtree is eligible for remote synchronization.
- The provider-selection boundary and the worker-control-plane boundary are separate; merging them into one vaguely named local gateway would contaminate the routing experiment.
