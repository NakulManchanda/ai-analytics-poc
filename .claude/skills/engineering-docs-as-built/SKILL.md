---
name: engineering-docs-as-built
description: Create, review, or update concise external-facing engineering documentation grounded in repository code, configuration, and measured evidence. Use for requests to document an architecture, subsystem, implementation, README, or developer guide; clean up user-facing engineering docs; or explain an implemented system to readers. Also use when explicitly invoked as engineering-docs-as-built. Do not invoke for ordinary refactoring, code review, internal plans, or PR reviews unless documentation is requested.
---

# Engineering Docs — As Built

Make the **implemented system understandable and verifiable** to engineers who have not attended its development. This skill owns the *quality of user-facing docs*, not runtime changes. Follow the repository's `AGENTS.md` for change and PR policies. For routine documentation of a small code diff, use `document-diff` instead.

## Workflow

1. **Discover the entry point.** Read the current README/index, canonical page, nearby docs, and relevant source; find the implementation owners rather than relying on old prose.
2. **Verify claims.** Inspect routes, orchestration, defaults, runtime manifests, env vars, tests, evaluations, and experiment artifacts as applicable. Form an internal claim → file → status → evidence map. Missing evidence means qualify or omit the claim.
3. **Select the reader's story.** Identify purpose, default/selected path, request flow, ownership boundaries, meaningful configuration and rationale, supported evaluation findings, limitations, and source links. Put the most useful evaluated decision near the top.
4. **Draft concisely.** Prefer a short introduction, one appropriate diagram/table, focused engineering decisions, scoped measured findings, and implementation references. Skip sections that do not help this document.
5. **Review adversarially.** Check against source, remove repetition, stale names, development chronology, issue/course context, private chat artifacts, overclaimed performance, and generic filler.
6. **Validate.** Check referenced code paths and relative Markdown links; run `python .claude/skills/engineering-docs-as-built/scripts/validate_doc_links.py <changed-markdown-files>`. Inspect Mermaid syntax manually or use an available renderer. Do not claim tests ran unless they did.
7. **Publish with scope control.** Modify canonical pages and the README index only when useful. Follow repo branch/PR rules; never merge unless authorized or explicitly allowed by repository policy.

## Required distinctions

- **As built vs future:** explicitly label default, implemented optional, experimental, proposed, and unproven paths. Do not imply a rendered configuration is deployed or a lab experiment succeeded.
- **Observed vs inferred:** distinguish metrics observed in a particular experiment from general conclusions. “Best evaluated” must name the relevant comparison/workload; avoid unqualified superlatives.
- **Mechanism boundaries:** separate concepts a reader could confuse (e.g. gateway prefix-affinity routing vs vLLM prefix caching vs cross-worker KV transfer; startup health vs completed inference warmup).
- **Important configuration:** include settings that affect correctness, cost, reliability, capacity, security, or performance; explain why they matter. Do not dump full YAML.
- **Correct vocabulary:** use terms the implementation supports. Do not call SQL/MCP tool-grounded analytics vector/document RAG without actual chunking, embeddings, or a vector index.
- **Audience:** write for an external engineer, not the author. Do not import conversation-specific instructions, work-history entries, issue IDs, course language, or unrequested internal planning into public docs.
- **No invented improvements:** future opportunities may be one short, explicitly labeled section if genuinely helpful.

## Presentation choices

- **Table:** component responsibilities, model comparisons, runtime config with rationale, implementation vs experimental status.
- **Mermaid diagram:** request flows and control/data-plane boundaries only when it clarifies relationships; no decorative diagrams.
- **Highlight:** a single important default/best-evaluated path near the introduction when the evaluation justifies it.
- **Progressive disclosure:** README → concise architecture page → experiments/runbooks → manifests/code. Preserve source-of-truth detail in its own layer.

## Suggested page shape (adapt, never force)

1. Purpose / scope (1–2 sentences)
2. Default or evaluated path (when applicable)
3. Flow / components
4. Design choices and selected configuration *with reasons*
5. What the evidence demonstrates (and does not)
6. Implementation and experiment links

## References

- Read [writing standards](references/writing-standards.md) for a new or substantially rewritten public architecture page.
- Read [before/after examples](references/examples.md) when choosing wording for a measured baseline, a configuration rationale, or status distinctions.
- Use the link validator after changes. It validates local link targets, not implementation correctness.

## Completion criteria

- Default path and feature status agree with code and manifests.
- Every significant engineering claim is traceable to authoritative sources.
- Technical choices explain **why** and limits of evidence are explicit.
- An unfamiliar engineer can navigate from README to the implementation.
- No secrets, machine-specific paths, unsupported deployment claims, or irrelevant project history.
