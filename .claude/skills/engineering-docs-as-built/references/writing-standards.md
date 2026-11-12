# User-facing engineering documentation standards

## Write for discovery

- Start with what the subsystem does and the *actual default/selected path*. Avoid starting with historical tickets, milestones, course deliverables, or a changelog.
- Show boundaries and responsibilities; use exact names of code-owned modules and interfaces.
- Provide stable relative links to source files/configuration rather than copying whole files.
- Use compact tables for “component → responsibility”, “setting → value → reason”, or “alternative → verified status”.
- Use Mermaid only when execution order, branching, or component relationships are easier to understand visually.
- Prefer concise pages (~400–800 words for a subsystem overview); length may increase when justified by meaningful technical decisions.
- Keep detailed commands, evidence datasets, lengthy results, and reproduction procedures in linked runbooks.

## Evidence labels

| Label | Meaning |
| --- | --- |
| Default / baseline | Actual configured or selected normal execution path |
| Implemented optional | Supported but not the default; say how it is enabled |
| Experimental | Controlled or opt-in mechanism with incomplete general validation |
| Planned | Not implemented |
| Measured | Supported by specific recorded experiment and conditions |
| Unproven | Code/config may exist but the claimed behavior or benefit has not been demonstrated |

## Claims audit before publishing

- Does the diagram reflect runtime control and data flow?
- Do the exact env var and CLI flag values match manifests?
- Is a source filename/URL correct on the target branch?
- Are multi-model choices supported by evals rather than preference?
- Is a “readiness” claim about health checks, real inference, or user-visible latency?
- Is a cache “hit” inferred from routing or shown by actual worker evidence?
- Is an experimental connector merely rendered/configured, or was its transfer consumption validated?
- Has the README index been updated only when discovery benefits?

## Anti-patterns

- A 20-page architectural page containing runbooks, test logs, old ticket status, and implementation history.
- Implying a vector retrieval index exists in a tool-based SQL analytics system.
- Naming a future goal as already deployed.
- Listing configuration numbers without explaining their operational effect.
- Calling a model “best” without stating the workload and evidence.
- Creating duplicate canonical docs instead of updating the existing one.
