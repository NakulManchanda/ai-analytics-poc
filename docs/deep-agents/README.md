# Deep Agents improvements (#226, epic #224)

Why Deep Agents trails the manual loop on the 43 golden turns, and the five improvements chosen to close the gap. Detail for step 1 is in [01-tool-contract.md](01-tool-contract.md); the rest is here.

## Starting point (v5, grader v3, default budgets)
| | Manual (oct31) | Deep Agents v5 |
|---|---|---|
| completed / budget stop / failed | 40 / 0 / 3 | 41 / 2 / 0 |
| tool correct | 21 | 12 |
| execution correct (of 40) | 38 | 31 |
| answer correct | 14 | 19 |
| model calls / tool calls | 65 / 36 | 86 / 61 |
| input tokens per turn | 2.9k | 8.6k |
| cost | $0.102 | $0.332 |

Deep Agents answers as well as manual but spends about 3x the tokens and many more steps. Only a minority of the tool/execution gap came from one error class (see step 1).

## The five improvements
| # | Improvement | Status |
|---|---|---|
| 1 | **Fix the tool contract.** Empty `dimensions` means one overall total (MCP validator, app validator, Deep Agents parser/schema); an omitted `dimensions` means the same; the server's validation message reaches the model; grader v4 accepts an omitted dimension pinned by an equality filter | Implemented. Measured once (below); the later fixes are not measured yet. |
| 2 | **Cut per-call overhead.** Measured where the tokens go; trimmed the duplicated filters schema in `compare_taxi_segments`; turned on Bedrock prompt caching in the LiteLLM config | Implemented. Caching proven with live calls (below); schema trim not yet measured in an eval. `excluded_tools` investigated and not applied (nothing left to exclude). |
| 3 | **Separate model effect from harness effect.** `AGENT_HARNESS=create_agent` swaps `create_deep_agent` for LangChain's plain `create_agent` with the same tools, prompt and allowlist | Switch implemented (default off, tested). The comparison run and any model change are **not done**: a model switch needs your decision and provisioning. |
| 4 | **Guard against fabricated tool answers.** A final answer containing `<untrusted_tool_data` with no successful tool call is replaced by a "no verified figures" message and a failed `grounding_check` step is recorded | Implemented and tested. |
| 5 | **Do not spend effort on** raising token/tool-call caps, summarization, planning | No code. Highcap runs did not help; summarization never fires (trigger 170k tokens, tool results are 1.4% of input); planning stays off by default (`AGENT_PLANNING=1`), but the #226 issue wants it compared, so that comparison is still open. |

Two gaps are in none of the five and are the likely largest accuracy lever: about 14 "expected tool not matched" turns and 6 step-budget breaches (details in the bigger-picture section of doc 01). They need a turn-by-turn read before any fix.

## The roughly 14 "expected tool not matched" turns, classified (empty-dims-v1)
- **Legacy golden tool, 3 turns** (`det-004`, `det-005`, `multi-001#0`): the golden expects `query_taxi_data`; Deep Agents is not offered that tool and used the equivalent `aggregate_taxi_data`. A golden/grader matter that affects the manual path equally. Not changed here.
- **Genuine model errors, 4 turns:** `det-011` used `average_tip` for a "tip rate" question (`tip_rate` exists; measures had no descriptions), `react-002` requested one of two measures, `react-006` filtered `rate_code` instead of `pickup_zone = JFK Airport`, `det-001` set `include_column_stats: true`.
- **Extra or split calls, rest:** one aggregate call per figure (`react-005` 4 calls, `react-008` 3, `react-001` 3) instead of one call with several measures or a comparison; these are also most of the 6 step-budget breaches.
- Changes made for these: a short measure glossary and `order_by`/place-name guidance in the `aggregate_taxi_data` description, a "put every measure in one call" line for `compare_taxi_segments`, an `include_column_stats` line for `describe_taxi_dataset`, and one sentence in the system prompt asking for as few tool calls as possible. Whether they work is unmeasured.

## Step 2 findings (measured)
- The system prompt is only about 930 characters. The built-in Deep Agents tools are already filtered out by the allowlist, so `excluded_tools` or a harness profile would remove nothing more. Not changed.
- The four MCP tool definitions are about 11k characters (about 2.7k tokens) of a roughly 4k-token first call. The same filters schema (about 2.5k characters) appears three times: `aggregate_taxi_data.filters`, and `compare_taxi_segments` `baseline_filters` and `comparison_filters`. The second copy in `compare_taxi_segments` is now a short pointer to the first (about 2.5k characters, roughly 600 tokens, less per call). The server still validates every key. Risk: the model could build the comparison filters worse; the next run shows.
- Prompt caching was off: across all 1,412 calls in the v5 spend logs, cache creation and cache read tokens are 0. `infra/litellm/config.yaml` now sets `cache_control_injection_points` (a cache point after the system message) on `taxi-planner` and `taxi-answer`. Bedrock orders the prompt tools, then system, then messages, so the cached prefix is the tools plus the system prompt (about 4k tokens, above the minimum). Proven live through the proxy with two identical requests on `taxi-answer`: the first wrote 6,639 tokens to the cache, the second read 6,639 from it, and the response cost fell from $0.000233 to $0.0000589 (-75%). **This applies to the manual path as well** (same aliases), and the cache expires after a few minutes without reuse. Real-run savings are not measured yet; capture cost uses LiteLLM's billed cost, so reconciliation should still match.

## First measured run: `deep-agents-empty-dims-v1` (committed code at `c5f436a`)
Contract change, hint, grader v4 and the guard only. Not included: omitted-`dimensions` handling, server messages to the model, the compare schema pointer, the harness switch.

| | v5 (grader v3) | empty-dims-v1 (grader v4) |
|---|---|---|
| completed / budget stop | 41 / 2 | 42 / 1 |
| tool.failed | 6 | 5 |
| tool calls / model calls | 61 / 86 | 64 / 93 |
| execution / tool / answer correct | 31 / 12 / 19 | 31 / 12 / 18 |
| input tokens per turn | 8.6k | about 9.4k |
| cost | $0.3323 | $0.3604 |
| trace check | 0 problems | 0 problems |

Reading it:
- The `dimensions: []` rejections are gone, but the headline numbers did not move. The old error class was a small share of the gap, as the breakdown predicted.
- The 5 remaining failures were different causes, found by reading the calls:
  - `uns-001` and `uns-002` sent `dimensions: []` with `order_by` on `pickup_date`, which the server rejects ("order_by.key must be one of the requested output columns"). The model saw only the reason code, not that message, and in `uns-002` repeated the call until the repeat guard stopped the run.
  - `react-003` omitted `dimensions` completely (twice) and called `compare_taxi_segments` without `segment_dimension`; the server message again never reached the model.
- Hence the two follow-up fixes in step 1: pass the server's validation message to the model for error envelopes, and treat an omitted `dimensions` as `[]`.
- The cost went up slightly because the model made more calls, not because the contract change cost anything.
- Not like-for-like: grader v3 vs v4; older runs need regrading.

## Not done
- **Model change** (Nova Pro for every call vs Micro for the answer, or a model on LangChain's suggested list): needs provisioning and your decision; a listed profile is not access.
- **`create_agent` comparison run** with `AGENT_HARNESS=create_agent`.
- **Golden/grader fixes** for the legacy `query_taxi_data` expectations.
- Planning comparison (`AGENT_PLANNING=1`), as the #226 issue asks.

## Next measurement
One more run, `deep-agents-empty-dims-v2`, on the current branch head (all of steps 1, 2 and 4). Then, if wanted, a `create_agent` run with `AGENT_HARNESS=create_agent` for step 3. Commands are in doc 01.
