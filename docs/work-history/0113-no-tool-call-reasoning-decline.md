# 0113 — blank-content planner declines (#186 Slice C)

## Goal
Stop four golden turns (uns-004, uns-006, multi-003#0, fail-004) from ending in `run.failed: no_tool_call`, without reading or exposing the model's hidden reasoning.

## Starting point
Baseline 2 failed the same four turns every run. The first hypothesis was that the planner answered in prose that the phrase classifier missed.

## Decisions
- Replaying the four requests straight to LiteLLM (8 calls, about $0.0012) showed the premise was wrong: `content` is blank and the decline sits in `reasoning_content`/`thinking_blocks` (completion tokens 88/80/113/96, matching the live runs). The classifier looked only at `content`, saw "", returned `no_tool` and raised.
- AGENTS.md forbids exposing hidden chain-of-thought, so the reasoning text is not used at all, not even transiently. Classification is structural: no tool call, blank `content` and reasoning present (non-empty `reasoning_content`, non-empty `thinking_blocks`, or `reasoning_tokens > 0`) returns the existing `no_tool` outcome with `decline_reason=None`. The existing loop path already answers `no_tool` with a fixed, safe message and emits `run.declined` with a null reason.
- Recorded facts only: `reasoning_present`, `reasoning_chars` (a length), `reasoning_tokens` (already a field) and `decline_basis` (`content` | `reasoning`), on the proposal, the `LLMCall` metadata and the capture model_call record, added only when a decline sets `decline_basis`.
- Blank content with no reasoning still raises `no_tool_call`; tool-call, `multiple_tool_calls`, `malformed_tool_use` and content-based paths are unchanged. The change sits in `ServeLLMClient._post_tool_proposal`, which `LiteLLMClient` wraps and inherits.
- Not chosen: an `unsupported` outcome with a new template. The app cannot tell a correct decline (uns-004, uns-006) from a wrong one (multi-003#0, fail-004, where the data exists) without reading the reasoning. Making that distinction needs a readable decline from the model, which is a prompt or thinking-setting change for #179.

## Verification
- App suite 456 passed (+7 new in `test_no_tool_reasoning.py`); ruff clean. The tests cover blank content plus reasoning, `reasoning_tokens` alone, blank content with no reasoning, unchanged content and tool-call paths, an end-to-end loop run, and a capture record; a SECRET marker in `reasoning_content` never reaches the answer, events, run metadata, messages, logs or `captures.jsonl`.
- Grader reading: the four turns complete as `no_tool`. uns-004 and uns-006 expect no tool, so they grade tool-correct; their answer grade is `needs_review` because the fixed text has no limitation phrase. multi-003#0 grades tool-incorrect, which is the honest result for #179.
- Not run: mypy (the repo has none), the other service suites, and any live Bedrock or docker validation (owner runs the four-question replay and a fresh `eval-all`; expected `run.failed` 7 to 3).

## PR / merge state
Draft PR, `Refs #186`; the issue stays open (Slice B, det-006, is next).

## Lessons
- Replay the failing requests before designing a classifier: the model's answer was in a field the app never read, not in prose.
- When a provider splits output into `content` and `reasoning_content`, check both before blaming the classifier.
