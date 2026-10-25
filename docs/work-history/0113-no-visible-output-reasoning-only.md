# 0113 — blank-content, reasoning-only planner output (#186 Slice C)

## Goal
Stop four golden turns (uns-004, uns-006, multi-003#0, fail-004) from ending in `run.failed: no_tool_call`, without reading or exposing the model's hidden reasoning and without claiming something the app cannot know.

## Starting point
Baseline 2 failed the same four turns every run. The first hypothesis was that the planner answered in prose that the phrase classifier missed.

## Decisions
- Replaying the four requests straight to LiteLLM (8 calls, about $0.0012) showed the premise was wrong: `content` is blank and the model's output sits in `reasoning_content`/`thinking_blocks` (completion tokens 88/80/113/96, matching the live runs). The classifier looked only at `content`, saw "", returned `no_tool` and raised.
- AGENTS.md forbids exposing hidden chain-of-thought, so the reasoning text is not used at all, not even transiently. Only presence, a character count and `reasoning_tokens` are recorded.
- First design (superseded): map blank content plus reasoning to `no_tool`, a decline. Review (changes requested) found this unsound: blank content plus reasoning also happens for multi-003#0 and fail-004, where the data exists, so it is not necessarily a decline. Without reading the reasoning the app cannot tell the two apart.
- Revised design: a distinct typed outcome `no_visible_output` (added to `PLANNER_OUTCOMES` and `NON_TOOL_OUTCOMES`). It is returned when there is no tool call, blank `content` and reasoning present. The loop treats it as its own terminal branch: the run completes, `run.declined` is emitted with `outcome="no_visible_output"` and a null reason (the existing terminal non-tool event), and the user sees a fixed message that claims neither a decline nor a limitation: "The model did not return a usable response for that question. Please try again or rephrase it." The `no_tool` message is unchanged.
- `output_basis` (renamed from `decline_basis`) says what the outcome rested on: `content` (content-based outcome, unchanged behavior), `reasoning_only` (blank content, reasoning, finish_reason not `length`) and `reasoning_length` (same, but `finish_reason == "length"`, i.e. generation was exhausted, likely reasoning consumed `max_tokens`). Both reasoning values give `no_visible_output`; `finish_reason` is recorded as before so the two are distinguishable.
- Carried through: the proposal result, `LLMCall` metadata (`reasoning_present`, `reasoning_chars`, `output_basis`, `outcome`), the `llm_proposal` run-step metadata, `telemetry.planner_outcome`/`rejected_outcome`, and the capture `model_call` record. These facts are emitted only when `output_basis` is set.
- Blank content with no reasoning still raises `no_tool_call`; tool-call, `multiple_tool_calls`, `malformed_tool_use` and content-based paths are unchanged. No retries, no new classifier, no prompt or tool-selection change. The change sits in `ServeLLMClient._post_tool_proposal`, which `LiteLLMClient` wraps and inherits.
- `tooling/evals` was reviewed and not changed: graders and run-review/compare read grades and `terminal`, not planner outcomes, and the failure histogram counts only `run.failed` rows. The four turns now complete, so they leave that histogram; the outcome is visible in each run's events, step metadata and captures.

## Verification
- App suite 457 passed (8 in `test_no_tool_reasoning.py`); ruff clean; black clean on changed files. Tests cover blank content plus reasoning with `stop` and with `length`, `reasoning_tokens` alone, blank content with no reasoning (`no_tool_call`), unchanged content and tool-call paths, an end-to-end loop run (fixed answer, run completes, no `run.failed`, no claim of a decline, outcome in event, step metadata and `LLMCall` metadata) and a capture record. A SECRET marker in `reasoning_content`/`thinking_blocks` never reaches the answer, events, run metadata, steps, messages, logs or `captures.jsonl`.
- Grading expectation: the four turns complete as `no_visible_output`. uns-004 and uns-006 expect no tool, so they grade tool-correct; their answer grade is `needs_review` because the fixed text has no limitation phrase. multi-003#0 grades tool-incorrect, which is the honest result for #179. None is claimed as a confirmed decline.
- Not run: mypy (the repo has none), the evals suite (tooling/evals untouched), the other service suites, and any live Bedrock or docker validation (the owner runs the four-question replay and a fresh `eval-all`; expected `run.failed` 7 to 3).

## PR / merge state
Draft PR, `Refs #186`; the issue stays open (Slice B, det-006, is next).

## Lessons
- Replay the failing requests before designing a classifier: the output was in a field the app never read, not in prose.
- Absence of visible content is not evidence of a decline; type it as its own outcome rather than borrowing a semantic label.
