# Validation and grounding: deterministic vs probabilistic

Issue #180. The model is probabilistic and untrusted; security, spend and factual
checks are deterministic Python outside the model. Prompt wording is not a control.

## Boundary

| Concern | Owner | Mechanism |
| --- | --- | --- |
| Which tool/arguments run | Deterministic | `app.validation.validate_proposal` (allowlist, types, enums, borough/zone/time scope, max one governed analysis) then MCP re-validation and authorization (authoritative) |
| Retry after a bad proposal | Deterministic policy | `retry_allowed`: one retry, recoverable shape/argument errors only, only if LLM-call, token and time budgets still cover retry + final answer |
| Auth, tenant, budget, deadline, unsupported request | Deterministic, never retried | MCP errors, `BudgetTracker`, `out_of_scope_time` and `max_governed_analyses_exceeded` violations |
| Numbers, percentages, money, cited query ids in the answer | Deterministic | `check_answer_grounding` against the turn's tool observation (direct values plus sums, differences, ratios, shares); mismatches append a fixed caveat and record a `grounding_check` step |
| Unsupported causal claims | Deterministic flag (advisory) | causal phrasing without hedging is flagged; the dataset has no causal evidence |
| Instruction-like text in tool data | Deterministic audit signal | `scan_observation` records an `observation_scan` step; never edits values or gates execution |
| Choosing the analysis, phrasing, helpfulness, tone | Probabilistic | The model, scored by the calibrated LLM judge (subjective quality only) |

## Flow

1. Planner proposes a tool (or the catalogue supplies one).
2. `validate_proposal` runs. Invalid proposals never reach MCP; the pre-execution gate also checks catalogue entries.
3. If every violation is recoverable and budgets allow, the proposal is re-requested once with deterministic feedback (violation code and template text only; model-supplied strings are never echoed). A retry makes no tool call, so it cannot replay a side effect.
4. The governed tool executes at most once per run; MCP enforces authorization.
5. Tool output is untrusted data. Nothing parses tool calls out of observations or answers.
6. The answer is checked against the evidence before it is finalized.

## Known limits

- The pre-flight allowlists mirror `dataset_spike.query_spec`; a catalogue test catches drift. MCP stays the source of truth.
- Time scope is the pinned month in the schema; zone names are bounded text, validated by MCP.
- Grounding is syntactic: it cannot judge whether a true number answers the question, and small integers (0-31) and years are not checked. Causal detection is phrase based.
- Numeric grounding is syntactic: a number may occur in, or be derivable from, the observation without proving it belongs to the right zone, metric or period.
- The validation retry reserves roughly twice the previous proposal's token usage for the retry and the final answer. That is an estimate, not a guaranteed reservation: the final answer can use more tokens than the previous proposal. The runtime budgets (`BudgetTracker`) still enforce actual consumption, so an under-reserved retry can leave the run without a full answer.
- Grounding checks do not guarantee a supported number is tied to the correct metric or geographic region; a wrongly associated number can still pass.
- Rejected proposals persist only violation codes, argument keys and a SHA-256 of the arguments; raw values appear only with `CAPTURE_CONTENT` enabled (step metadata).
- Retry feedback is appended to the user question argument; prompt templates are owned by #178 and not changed here.
- No few-shot examples were added: none is tied to a validated failure cluster yet.
