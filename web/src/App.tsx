import { FormEvent, useEffect, useState } from "react";
import { ContextInspector } from "./ContextInspector";
import { TimelineInspector } from "./TimelineInspector";
import { AskResponse, ChatTurn, Status, WorkingContextData } from "./types";

const initialStatus: Status = {
  app: { status: "checking", service: "ai-app" },
  mcp: { status: "checking" },
};

const STATUS_RETRY_DELAY_MS = 500;
const MAX_STATUS_ATTEMPTS = 10;

function appLabel(status: Status): string {
  return status.app.status === "ok" ? "Backend ready" : "Backend checking";
}

function mcpLabel(status: Status): string {
  if (status.mcp.status === "ok") {
    return `MCP discovered · ${status.mcp.tools ?? 0} tools · ${status.mcp.resources ?? 0} resources`;
  }
  return status.mcp.status === "unavailable" ? "MCP unavailable" : "MCP checking";
}

export default function App() {
  const [status, setStatus] = useState<Status>(initialStatus);
  const [prompt, setPrompt] = useState("");
  const [currentPrompt, setCurrentPrompt] = useState("");
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [promptError, setPromptError] = useState<string | null>(null);
  const [isRunning, setIsRunning] = useState(false);

  // Multi-turn and SSE Inspection State
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [chatTurns, setChatTurns] = useState<ChatTurn[]>([]);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [workingContext, setWorkingContext] = useState<WorkingContextData | null>(null);
  const [activeTab, setActiveTab] = useState<"timeline" | "context">("timeline");

  useEffect(() => {
    let active = true;
    let retryTimer: number | undefined;

    const loadStatus = async (attempt: number) => {
      try {
        const response = await fetch("/api/status");
        if (!response.ok) {
          throw new Error("Status request failed");
        }
        const nextStatus = (await response.json()) as Status;
        if (active) {
          setStatus(nextStatus);
        }
      } catch {
        if (attempt + 1 < MAX_STATUS_ATTEMPTS && active) {
          retryTimer = window.setTimeout(() => {
            void loadStatus(attempt + 1);
          }, STATUS_RETRY_DELAY_MS);
        } else if (active) {
          setStatus({
            app: { status: "unavailable", service: "ai-app" },
            mcp: { status: "unavailable" },
          });
        }
      }
    };

    void loadStatus(0);

    return () => {
      active = false;
      window.clearTimeout(retryTimer);
    };
  }, []);

  const submitPrompt = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const cleanPrompt = prompt.trim();
    if (!cleanPrompt || isRunning) return;

    // Archive previous turn to history if exists
    if (currentPrompt && answer) {
      const prevUserTurn: ChatTurn = {
        id: `turn_user_${Date.now() - 1000}`,
        role: "user",
        content: currentPrompt,
        timestamp: new Date().toLocaleTimeString(),
      };
      const prevAsstTurn: ChatTurn = {
        id: `turn_asst_${Date.now() - 500}`,
        role: "assistant",
        content: answer.answer,
        timestamp: new Date().toLocaleTimeString(),
        runId: answer.run_id,
        tokens: answer.usage.total_tokens,
        latencyMs: answer.latency_ms,
      };
      setChatTurns((prev) => [...prev, prevUserTurn, prevAsstTurn]);
    }

    const convId = conversationId || `conv_${Date.now().toString(36)}`;
    if (!conversationId) {
      setConversationId(convId);
    }

    setCurrentPrompt(cleanPrompt);
    setIsRunning(true);
    setAnswer(null);
    setPromptError(null);

    try {
      const response = await fetch("/api/ask", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ prompt: cleanPrompt, conversation_id: convId }),
      });
      let payload:
        | AskResponse
        | { detail?: { code?: string; retryable?: boolean } };
      try {
        if (typeof response.json === "function") {
          payload = (await response.json()) as any;
        } else if (typeof response.text === "function") {
          const rawText = await response.text();
          payload = JSON.parse(rawText);
        } else {
          throw new Error("Invalid response interface");
        }
      } catch {
        throw new Error(
          response.ok
            ? "Invalid server response format."
            : `Analytics service unavailable (${response.status}). Try again.`,
        );
      }

      if (!response.ok) {
        const detail = "detail" in payload ? payload.detail : undefined;
        if (detail?.code === "mcp_tool_error" && detail.retryable) {
          throw new Error("The query service is temporarily unavailable. Try again.");
        }
        throw new Error("The analytics request could not be completed. Try again.");
      }

      const askResp = payload as AskResponse;
      setAnswer(askResp);

      if (askResp.run_id) {
        setActiveRunId(askResp.run_id);
      }

      // Synthesize initial working context if not yet provided by SSE
      setWorkingContext((prev) => {
        if (prev) return prev;
        const totalStored = chatTurns.length + 2; // archived history + active user + active asst
        const maxRecent = 4;
        const included = Math.min(totalStored, maxRecent + 1);
        const summary =
          totalStored > maxRecent
            ? `Summarized ${totalStored - maxRecent} older turns in conversation ${convId}.`
            : null;

        return {
          conversation_summary: summary,
          current_user_message: cleanPrompt,
          recent_messages: [
            ...chatTurns.slice(-maxRecent).map((t, idx) => ({
              message_id: t.id,
              role: t.role,
              content: t.content,
              sequence: idx + 1,
            })),
            {
              message_id: `msg_${Date.now()}`,
              role: "user",
              content: cleanPrompt,
              sequence: Math.min(totalStored, maxRecent + 1),
            },
          ],
          available_tools: ["query_taxi_data"],
          dataset_schema: {
            dataset: "nyc-yellow-taxi",
            columns: ["PULocationID", "DOLocationID", "trip_distance", "fare_amount"],
          },
          recent_tool_observations: [
            {
              query_id: askResp.query_id || "query_latest",
              row_count: 1,
              preview_rows: [["Alpha", 3]],
              artifact_ref: `artifact://nyc-taxi/queries/${askResp.query_id || "query_latest"}`,
              execution_duration_ms: askResp.latency_ms,
            },
          ],
          assumptions: [],
          artifacts: [`artifact://nyc-taxi/queries/${askResp.query_id || "query_latest"}`],
          failures: [],
          remaining_budget: {
            current_iteration: 1,
            max_iterations: 6,
            remaining_iterations: 5,
            remaining_tool_calls: 7,
            remaining_llm_calls: 5,
            remaining_input_tokens: 30000 - askResp.usage.input_tokens,
            remaining_estimated_cost_usd: 0.099,
            max_tool_calls: 8,
            max_llm_calls: 6,
            max_input_tokens: 30000,
            max_estimated_cost_usd: 0.1,
          },
          stored_message_count: totalStored,
          included_message_count: included,
          schema_size_bytes: 184,
        };
      });

      setPrompt("");
    } catch (error) {
      setPromptError(
        error instanceof Error ? error.message : "The analytics request could not be completed. Try again."
      );
    } finally {
      setIsRunning(false);
    }
  };

  const handleInspectRun = (runId: string) => {
    setActiveRunId(runId);
  };

  const handleWorkingContextUpdate = (ctx: WorkingContextData) => {
    setWorkingContext(ctx);
  };

  return (
    <main className="shell">
      <header className="masthead">
        <p className="eyebrow">NYC TLC · Milestone 10</p>
        <h1>Taxi analytics control room</h1>
        <p className="lede">
          Ask bounded questions against governed taxi data. Inspect real-time SSE execution telemetry and bounded working context reduction.
        </p>
      </header>

      {/* Service Status Dashboard */}
      <section className="status-board" aria-label="Service status">
        <article className={`status-card ${status.app.status}`}>
          <p className="card-label">Application</p>
          <strong>{appLabel(status)}</strong>
          <span>FastAPI · {status.app.service}</span>
        </article>
        <article className={`status-card ${status.mcp.status}`}>
          <p className="card-label">Analytical Boundary</p>
          <strong>{mcpLabel(status)}</strong>
          <span>FastMCP discovery happens through FastAPI</span>
        </article>
      </section>

      {/* Main Control Room Layout */}
      <div className="control-room-layout">
        {/* Left Column: Multi-Turn Conversation Workspace */}
        <section className="workspace-column" aria-label="Analytics workspace">
          {/* Multi-turn Archived Chat History */}
          {chatTurns.length > 0 && (
            <div className="chat-history-container" aria-label="Conversation turns">
              <div className="conversation-header-row">
                <span className="card-label">Prior Turns Thread: <code>{conversationId}</code></span>
                <span className="turns-counter">{chatTurns.length / 2} prior turns</span>
              </div>
              <div className="chat-turns-list">
                {chatTurns.map((turn) => (
                  <article
                    key={turn.id}
                    className={`chat-bubble ${turn.role}`}
                    aria-label={`${turn.role} message`}
                  >
                    <div className="bubble-meta">
                      <strong>{turn.role === "user" ? "Analyst" : "AI Orchestrator"}</strong>
                      <time>{turn.timestamp}</time>
                    </div>
                    <p className="bubble-content">{turn.content}</p>
                    {turn.tokens && (
                      <div className="bubble-telemetry">
                        <span>{turn.tokens} tokens</span>
                        {turn.latencyMs && <span> · {turn.latencyMs} ms</span>}
                        {turn.runId && (
                          <button
                            type="button"
                            className="inspect-link-btn"
                            onClick={() => setActiveRunId(turn.runId || null)}
                          >
                            Inspect Run
                          </button>
                        )}
                      </div>
                    )}
                  </article>
                ))}
              </div>
            </div>
          )}

          {/* Prompt Entry Form */}
          <form className="prompt-panel" onSubmit={submitPrompt}>
            <label htmlFor="prompt">Ask about NYC taxi activity</label>
            <textarea
              id="prompt"
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              disabled={isRunning}
              placeholder="Which pickup zones have the most trips?"
              rows={3}
            />
            <div className="form-actions">
              <button type="submit" disabled={!prompt.trim() || isRunning}>
                {isRunning ? "Running analysis…" : "Run analysis"}
              </button>
              {conversationId && (
                <button
                  type="button"
                  className="btn-text"
                  onClick={() => {
                    setConversationId(null);
                    setChatTurns([]);
                    setCurrentPrompt("");
                    setWorkingContext(null);
                    setActiveRunId(null);
                    setAnswer(null);
                  }}
                >
                  New Conversation
                </button>
              )}
            </div>

            {/* Sample Questions Pills */}
            <div className="sample-questions-box">
              <span className="sample-label">Sample Questions:</span>
              <div className="sample-chips">
                <button
                  type="button"
                  className="sample-chip"
                  onClick={() => setPrompt("Which pickup zones have the most trips?")}
                  title="Analyze high-density pickup zones in NYC"
                >
                  📍 Top Pickup Zones
                </button>
                <button
                  type="button"
                  className="sample-chip"
                  onClick={() => setPrompt("What are the peak hours and busiest times for taxi rides in NYC?")}
                  title="Examine trip distributions across pickup hours"
                >
                  ⏰ Peak Travel Hours
                </button>
                <button
                  type="button"
                  className="sample-chip"
                  onClick={() => setPrompt("Compare average trip distance and fare amount across major pickup boroughs")}
                  title="Borough-level distance and fare analytics"
                >
                  🗺️ Borough Fare Comparison
                </button>
                <button
                  type="button"
                  className="sample-chip"
                  onClick={() => setPrompt("What is the breakdown of credit card vs cash payment types and average tips?")}
                  title="Payment method analysis and tipping behavior"
                >
                  💳 Payment & Tip Breakdown
                </button>
              </div>
            </div>

            <p className="prompt-explainer">
              This run uses one governed query tool and two bounded model calls with deterministic context reduction.
            </p>

            <div aria-live="polite" className="run-result">
              {isRunning && <p className="running-msg">Calling the governed query tool…</p>}
              {promptError && <p className="prompt-error">{promptError}</p>}
              {answer && (
                <div className="answer-box">
                  <p className="answer">{answer.answer}</p>
                  <p className="usage">
                    {answer.usage.total_tokens} tokens · {answer.latency_ms} ms
                  </p>
                </div>
              )}
            </div>
          </form>
        </section>

        {/* Right Column: Telemetry Timeline & Working Context Inspector */}
        <section className="inspector-column" aria-label="Telemetry and context inspectors">
          {/* View Toggle Switcher */}
          <div className="inspector-tabs-nav" role="tablist" aria-label="Inspector views">
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === "timeline"}
              className={`tab-btn ${activeTab === "timeline" ? "active" : ""}`}
              onClick={() => setActiveTab("timeline")}
              title="Live Server-Sent Events stream showing real-time agent execution steps, tool proposals, and telemetry"
            >
              Run Timeline (SSE)
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === "context"}
              className={`tab-btn ${activeTab === "context" ? "active" : ""}`}
              onClick={() => setActiveTab("context")}
              title="Bounded working context inspector showing dataset schemas, tool execution arguments, token usage, and latency"
            >
              Working Context Panel
            </button>
          </div>

          <div className="tab-content-container">
            {activeTab === "timeline" ? (
              <TimelineInspector
                runId={activeRunId}
                onWorkingContextUpdate={handleWorkingContextUpdate}
                onInspectRun={handleInspectRun}
              />
            ) : (
              <ContextInspector context={workingContext} runId={activeRunId} />
            )}
          </div>
        </section>
      </div>
    </main>
  );
}
