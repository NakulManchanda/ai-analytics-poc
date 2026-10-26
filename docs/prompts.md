# Versioned prompt templates

Issue #178 (epic #167). Instructions for the planner, answer and agent calls live in
`services/app/app/prompts/` as versioned bundles instead of inline strings. The first migration
changed **no rendered text**: golden tests (`services/app/tests/golden/prompts.json`, generated from
the pre-extraction code) prove byte-identical output. Validation, retry and grounding are #180's,
not this layer's.

## Model

- `PromptTemplate`: one region of a prompt, with a `kind` (system instructions, user content, tool
  observation, history, single-turn) and the variables that carry untrusted text. Rendering is
  single-pass `{{name}}` substitution: missing or unexpected variables raise `PromptRenderError`,
  and substituted values are never re-scanned, so text such as `{{system_prompt}}` inside a question
  or tool observation stays inert. System-instruction templates cannot declare untrusted variables.
- `PromptBundle`: what is versioned and attributed. One `template_id`, one immutable
  `MAJOR.MINOR.PATCH` version, its regions, and the bundles it embeds (`uses`).
- Regions stay separate: system instructions and trusted schema data go in the globally shared
  region; user text, prior turns and tool observations go in the conversation/suffix regions.
  Retrieved text is data and is never placed in the instruction region.
- `PromptAttribution(template_id, version, sha256)`: `sha256` is the hash of the exact
  instruction text the call sent. For partitioned prompts that is the globally shared region
  (instructions, rules, trusted schema); for single-turn prompts (direct Bedrock, LiteLLM planner)
  it is the whole rendered message. User content, history and observations are not part of the
  partitioned hash, so the same revision reproduces the same hash.

## Prompt path inventory

| Path | Calls a model | Bundle (`template_id@version`) |
|---|---|---|
| Catalogue / deterministic (`query_catalogue.lookup`) | No: planner step is skipped, no model-call record | none |
| Planner, serve/vLLM (`ServeLLMClient.propose_taxi_query`) | Yes | `planner@1.0.0` |
| Planner, LiteLLM proxy and direct Bedrock | Yes | `planner-single-turn@1.1.0` |
| Answer, serve/vLLM and LiteLLM (streaming or not) | Yes | `answer@1.0.0` (`answer-with-schema` when a schema is passed) |
| Answer, direct Bedrock (streaming or not) | Yes | `answer-single-turn@1.0.0` |
| Dataset profile answer, serve | Yes | `dataset-profile@1.0.0` |
| Dataset profile answer, direct Bedrock | Yes | `dataset-profile-single-turn@1.0.0` |
| Dataset profile proposal (`get_dataset_profile`) | No on serve (local); Bedrock sends the raw prompt | none |
| Direct ask, serve | Yes | `ask@1.0.0` |
| Direct ask, Bedrock `ask()` | Yes, raw caller prompt | none (not templated) |
| CrewAI researcher/writer (`agent_strategy=crewai`) | Yes (2 calls) | `agent-global@1.0.0` (shared instruction region only) |
| Local fake client | No model | none (never claims a prompt) |

Not yet templated (follow-up): CrewAI goal/backstory/task text (composed by CrewAI), the Bedrock
`ask()` passthrough, and the tool specs. Calls with no bundle record null attribution rather than a
guess. `prompt_version` on the run (`m9.v1`) is only the run-level prompt *config* tag; it is not a
template version.

## Where attribution is recorded (per LLM call)

- Run state: `Run.metadata["llm_calls"][i]` and each LLM `RunStep.metadata` carry
  `prompt_template_id`, `prompt_template_version`, `prompt_sha256` for that call (a failed call,
  such as `no_tool_call`, carries the revision it rendered).
- OTEL: one `ai.llm_call` span event per model call with `ai.prompt.template_id`,
  `ai.prompt.template_version`, `ai.prompt.sha256`, `ai.llm.role`, `ai.llm_call_id` and the alias.
- `capture.v1` `model_call` records: the same three fields plus `prompt_config_version` (the
  run-level tag). Metadata only: the rendered text is never captured, with or without
  `CAPTURE_CONTENT`, so the SHA-256 is the fingerprint in both modes.
- Evals: `tooling/evals` reads the fields and the captured-session report prints the revisions by
  role, so cost, tokens and quality compare per planner/answer revision. Model alias and prompt
  revision are separate dimensions; an alias is not a prompt version.

## Ownership

The application service owns templates (the MCP server never calls an LLM). A change to a bundle
is a change to model behaviour: it needs a PR, an eval run linked in the changelog below, and a
version bump.

## Changing a prompt (rollout)

1. Edit the text in `app/prompts/bundles.py`. Never reuse a version for different text.
2. Bump the bundle `version` (patch: wording that should not change behaviour; minor: behaviour
   change; major: contract/variable change). Editing `taxi-base` changes the fingerprint of every
   bundle that `uses` it, so those must bump too.
3. Update `services/app/tests/golden/template_pins.json` (id, version, fingerprint) and, where
   rendered text intentionally changed, the golden fixture. The pin test fails if text changes
   without a bump, and shows which bundle.
4. Add a changelog row below with the rationale and the eval run that supports it.
5. Run the baseline-vs-candidate eval report (`docs/evals.md`) comparing revisions per role.

## Rollback

Revert the commit that bumped the bundle (or ship a new higher version restoring the old text, if
the bad version already produced captures; versions are never reused). Captures keep their original
`template_id@version` and hash, so runs from the bad revision stay identifiable and excludable.

## Changelog

| Bundle | Version | Change | Eval run |
|---|---|---|---|
| all bundles | 1.0.0 | Extracted from inline strings; text unchanged (golden-tested) | n/a (no behaviour change) |
| planner-single-turn | 1.1.0 | Appended read-only decline, outside-the-dataset decline and positive tool-use anchor (#179); live eval-all pending | pending |
