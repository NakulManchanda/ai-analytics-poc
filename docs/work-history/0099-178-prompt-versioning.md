# 0099 — #178 versioned prompt templates (prompt PR 1/3)

## Goal
Make prompts independently versioned and attributable per model call, without changing any
rendered text or behaviour.

## Starting point
Instructions were inline in `prefix.py`, `llm.py` and `crewai_strategy.py`. Runs carried one
run-wide `prompt_version=m9.v1`, and capture copied it onto every record.

## Decisions
- A `PromptBundle` (id, immutable semver, regions, embedded bundles) is the versioned unit; the
  hash covers the exact instruction text sent. User text, history and observations stay in separate,
  untrusted regions; substitution is single-pass and strict.
- Golden fixture generated from the unmodified code proves byte-identical output; a pin file makes a
  text change without a version bump fail.
- Attribution is attached to each provider result where the prompt is rendered, so it follows the
  actual path (LiteLLM planner is `planner-single-turn`, serve planner is `planner`). The fake client
  and unversioned paths record null. Failed calls carry it via the provider error.
- Capture: `prompt_template_version` is now per call; the run tag moved to `prompt_config_version`
  (flagged semantic change inside `capture.v1`). Rendered text is not captured.
- OTEL: one `ai.llm_call` span event per call (there is no per-call span today).

## Verification
`uv run --project services/app pytest services/app/tests`, `make evals-test`, `make bench-test`,
black and ruff clean. See the PR for counts.

## PR / merge state
See the PR.

## Lessons
The first session question is a catalogue hit, so it has no planner call; attribution tests must
account for the deterministic path. Hashing the whole prompt would make every hash unique, so
partitioned calls hash only the instruction region.
