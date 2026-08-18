# Project execution rules

Build this project milestone by milestone.

- Read `docs/implementation-plan.md` and `docs/progress.md` before making changes.
- Work only on the currently requested milestone; do not pre-build future architecture.
- Prefer the smallest working vertical slice and finish it with tests or a manual check.
- Keep FastAPI orchestration and FastMCP as separate services once MCP is introduced.
- The application server owns all LLM calls; the MCP server never calls the LLM.
- Redis is transient coordination, never durable conversation state.
- Add infrastructure only when the active milestone requires it.
- Do not add Kubernetes, Kafka, RDS, EFS, OpenSearch, or a vector database.
- Never create or commit long-lived AWS credentials or secret values.
- Before marking a milestone complete, run its acceptance checks and update progress docs.
- Every post-bootstrap change uses a branch, worktree, pull request, and work-history entry.
- After a user correction, add a rule here only when it is reusable across future work.
