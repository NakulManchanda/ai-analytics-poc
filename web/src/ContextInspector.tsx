import React from "react";
import { WorkingContextData } from "./types";

interface ContextInspectorProps {
  context: WorkingContextData | null;
  runId?: string | null;
}

export const ContextInspector: React.FC<ContextInspectorProps> = ({ context, runId }) => {
  if (!context) {
    return (
      <section className="context-inspector-card empty" aria-labelledby="context-heading">
        <p className="card-label">Bounded Working Context</p>
        <h2 id="context-heading">Context Reducer Inspector</h2>
        <p className="empty-message">
          {runId
            ? "Waiting for context reduction event from SSE stream…"
            : "Run a query to inspect working context reduction and budget counters."}
        </p>
      </section>
    );
  }

  const isDiverged = context.stored_message_count !== context.included_message_count;
  const budget = context.remaining_budget;

  return (
    <section className="context-inspector-card" aria-labelledby="context-heading">
      <div className="inspector-header">
        <div>
          <p className="card-label">Milestone 10 · Bounded Working Context</p>
          <h2 id="context-heading">Context Reducer Inspector</h2>
        </div>
        <span className={`divergence-tag ${isDiverged ? "diverged" : "aligned"}`}>
          {isDiverged ? "Diverged: Stored != Context" : "Aligned: Stored == Context"}
        </span>
      </div>

      {/* 1. Durable State vs LLM Context Comparison */}
      <div className="context-metrics-grid">
        <div className="metric-box stored">
          <span className="metric-title">Stored Messages (DynamoDB)</span>
          <strong className="metric-value">{context.stored_message_count}</strong>
          <span className="metric-desc">Authoritative durable conversation history</span>
        </div>
        <div className="metric-box included">
          <span className="metric-title">Messages in LLM Context</span>
          <strong className="metric-value">{context.included_message_count}</strong>
          <span className="metric-desc">Bounded prompt payload sent to model</span>
        </div>
        <div className="metric-box schema">
          <span className="metric-title">Schema Context Size</span>
          <strong className="metric-value">{context.schema_size_bytes} B</strong>
          <span className="metric-desc">{context.dataset_schema?.columns?.length ?? 0} columns</span>
        </div>
      </div>

      {/* Core Architectural Invariant Callout */}
      <div className={`thesis-banner ${isDiverged ? "diverged" : ""}`} role="status">
        <div className="thesis-indicator" aria-hidden="true" />
        <div>
          <strong>Core AI-Systems Thesis: <code>durable conversation != current LLM context</code></strong>
          <p>
            {isDiverged
              ? `Older conversation turns are summarized outside the recent window to prevent context overflow, keeping ${context.included_message_count} of ${context.stored_message_count} messages in prompt.`
              : `All current conversation messages fit within the active ${context.included_message_count}-message working window.`}
          </p>
        </div>
      </div>

      {/* 2. Conversation Summary */}
      <div className="inspector-section">
        <div className="section-title-row">
          <p className="card-label">Conversation Summary</p>
          <span className="section-badge">Older Turns</span>
        </div>
        {context.conversation_summary ? (
          <div className="summary-box">
            <p>{context.conversation_summary}</p>
          </div>
        ) : (
          <p className="muted-note">No older turns summarized yet — active turns fit in working window.</p>
        )}
      </div>

      {/* 3. Recent Observations & Artifact References */}
      <div className="inspector-section">
        <div className="section-title-row">
          <p className="card-label">Recent Observations & Artifacts</p>
          <span className="section-badge">DuckDB MCP</span>
        </div>

        {context.recent_tool_observations && context.recent_tool_observations.length > 0 ? (
          <div className="observations-list">
            {context.recent_tool_observations.map((obs, idx) => (
              <div key={obs.query_id || idx} className="observation-card">
                <div className="obs-header">
                  <span className="obs-query-id">Query: <code>{obs.query_id}</code></span>
                  <span className="obs-meta">
                    {obs.row_count} rows · {obs.execution_duration_ms ?? 0} ms
                  </span>
                </div>

                {obs.preview_rows && obs.preview_rows.length > 0 && (
                  <div className="table-preview-wrapper">
                    <table className="preview-table">
                      {obs.columns && (
                        <thead>
                          <tr>
                            {obs.columns.map((col, cIdx) => (
                              <th key={cIdx}>{col}</th>
                            ))}
                          </tr>
                        </thead>
                      )}
                      <tbody>
                        {obs.preview_rows.map((row, rIdx) => (
                          <tr key={rIdx}>
                            {row.map((cell, cellIdx) => (
                              <td key={cellIdx}>{String(cell)}</td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}

                {obs.artifact_ref && (
                  <div className="artifact-ref-row">
                    <span className="artifact-label">Artifact Reference:</span>
                    <code className="artifact-code">{obs.artifact_ref}</code>
                  </div>
                )}
              </div>
            ))}
          </div>
        ) : (
          <p className="muted-note">No analytical observations in current working context.</p>
        )}

        {context.artifacts && context.artifacts.length > 0 && (
          <div className="artifacts-list">
            <span className="card-label">Referenced Artifacts:</span>
            <ul>
              {context.artifacts.map((art, aIdx) => (
                <li key={aIdx}><code>{art}</code></li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* 4. Execution Budgets & Iteration Counters */}
      {budget && (
        <div className="inspector-section">
          <div className="section-title-row">
            <p className="card-label">Run Execution Budgets</p>
            <span className="section-badge">Hard Boundaries</span>
          </div>

          <div className="budgets-grid">
            <div className="budget-item">
              <span className="budget-label">Current Iteration</span>
              <strong>{budget.current_iteration} / {budget.max_iterations}</strong>
              <div className="progress-track">
                <div
                  className="progress-fill iteration"
                  style={{ width: `${Math.min(100, (budget.current_iteration / budget.max_iterations) * 100)}%` }}
                />
              </div>
              <span className="budget-sub">{budget.remaining_iterations} iterations remaining</span>
            </div>

            <div className="budget-item">
              <span className="budget-label">Remaining Tool Calls</span>
              <strong>{budget.remaining_tool_calls} / {budget.max_tool_calls}</strong>
              <div className="progress-track">
                <div
                  className="progress-fill tools"
                  style={{ width: `${Math.max(0, (budget.remaining_tool_calls / budget.max_tool_calls) * 100)}%` }}
                />
              </div>
              <span className="budget-sub">Max {budget.max_tool_calls} queries / run</span>
            </div>

            <div className="budget-item">
              <span className="budget-label">Remaining Tokens</span>
              <strong>{budget.remaining_input_tokens.toLocaleString()} / {budget.max_input_tokens.toLocaleString()}</strong>
              <div className="progress-track">
                <div
                  className="progress-fill tokens"
                  style={{ width: `${Math.max(0, (budget.remaining_input_tokens / budget.max_input_tokens) * 100)}%` }}
                />
              </div>
              <span className="budget-sub">Input token budget</span>
            </div>

            <div className="budget-item">
              <span className="budget-label">Remaining Cost Budget</span>
              <strong>${budget.remaining_estimated_cost_usd.toFixed(4)} / ${budget.max_estimated_cost_usd.toFixed(2)}</strong>
              <div className="progress-track">
                <div
                  className="progress-fill cost"
                  style={{ width: `${Math.max(0, (budget.remaining_estimated_cost_usd / budget.max_estimated_cost_usd) * 100)}%` }}
                />
              </div>
              <span className="budget-sub">USD ceiling per run</span>
            </div>
          </div>
        </div>
      )}
    </section>
  );
};
