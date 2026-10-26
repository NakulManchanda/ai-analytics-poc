# 0114 — planner decline wording (#179)

## Goal
Requests that write data or that depend on causes outside the dataset should get a short plain-text decline, while answerable analytics questions must keep making their tool call.

## Starting point
On the LiteLLM/Bedrock planner path, "delete ..." and weather requests called unrelated analytics tools, and some runs returned blank outputs. The planner message (`planner-single-turn@1.0.0`) had no instruction about what to do when no governed analysis applies.

## Decisions
- Prompt wording only: `planner-single-turn` goes from 1.0.0 to 1.1.0 by appending three clauses after `Question: {{question}}` (read-only/cannot-change-data decline, outside-the-dataset decline, and a positive anchor that dataset questions must use a tool). No tool-description change, no keyword filter, no retry, no classifier.
- The shipped text is the measured variant F, copied verbatim from the experiment. The rendered message built by the app's own `_taxi_proposal` was diffed against the experiment's message (question + newline + F) for one sample question and is identical.
- The anchor is required. The text must not contain the phrase "call no tool": an earlier variant that did leaked it into answers.
- Golden prompt, template pin and attribution test expectations moved to 1.1.0; a focused test asserts the three clauses and the absence of "no tool". `docs/prompts.md` path inventory and changelog updated.
- Classifier fix: the 1.1.0 wording contains "cannot", "read-only" and "does not contain", which are in the `unsupported` phrase list, and `classify_non_tool_outcome(text, question)` scanned `question + text`, where llm.py passed the whole planner prompt as `question`. Every non-tool reply, even a blank one, became `unsupported` instead of `no_tool`. The `question` parameter is removed; both callers (LiteLLM/Serve and Bedrock-direct) now classify the response text only. This also keeps PR 203's `no_visible_output` path reachable with the new prompt, since blank replies stay `no_tool`. Interaction: the branch adding `no_visible_output` (PR 203) must merge first, then this branch rebases on it.

## Evidence (three local experiment rounds, scratch under `.vscode/myfiles`)
Round 3 (277 calls; outcome counts per variant, A = old wording, E = first wording, F = shipped, G = F without the anchor):
- Write requests (6 questions, n=18): F declined 15/18 (A 8, E 16).
- Causal/external requests (5 questions, n=15): F declined 8/15 (A 3, E 9).
- Controls (16 answerable questions, n=48): F made a tool call in 44/48 (A 37, E 42). The 4 misses were 3 HTTP 424 errors, all on "tip over 20 dollars", and 1 text answer.
- Leaked instruction text in declines: F 0, E 26 (E answered answerable questions with "... and call no tool"), A 0, G 0.
- G (no anchor) kept only 23/32 controls, so the anchor is needed.
Target questions under F: the weather question and both delete phrasings declined 3/3; "credit card trips" and "zone with most trips" kept their tool call 3/3.

## Known gaps
- Rush-hour traffic, news and subway-strike causal questions still call a tool in some runs (F: 7/15 of causal runs still called a tool).
- "remove trips shorter than 0.1 miles" still calls a tool (3/3 under F).
- Some delete declines read mismatched: F sometimes used the "dataset does not contain that information" wording for a write request.
- "tip over 20 dollars" fails (blank or HTTP 424) independent of this wording.
- Counts are directional: n=2-3 per cell, temperature-0 repeats are nearly one sample, and the schema in the experiment was representative, not the live one. Differences of 1-3 runs are noise.

## Verification
- `uv run --project services/app pytest services/app/tests -q`: 452 passed.
- `uv run --project services/app ruff check services/app tests services/gateway`: all checks passed.
- `uv run --project services/app black --check` on the changed Python files: unchanged.
- `make evals-test`: 120 passed, 2 skipped.
- Not run: the live eval-all against Bedrock is pending.

## PR / merge state
Uncommitted at the time of writing; PR not yet opened.

## Lessons
- Instruction text written in the imperative ("call no tool") can be echoed back to users on unrelated questions; third-person wording plus a positive anchor avoided it.
- Count outcomes structurally (tool call, decline, blank, error) and report misses next to the wins.
