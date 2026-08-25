import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { ContextInspector } from "./ContextInspector";
import { TimelineInspector } from "./TimelineInspector";
import {
  AskResponse,
  ChatTurn,
  ConversationSnapshot,
  RunTelemetry,
  Status,
  WorkingContextData,
} from "./types";

const initialStatus: Status = {
  app: { status: "checking", service: "ai-app" },
  mcp: { status: "checking" },
};

const STATUS_RETRY_DELAY_MS = 500;
const MAX_STATUS_ATTEMPTS = 10;
const CONVERSATION_STORAGE_KEY = "ai-analytics-conversation-id";

function metricValue(value: number | null | undefined, suffix = ""): string {
  return typeof value === "number" ? `${value}${suffix}` : "Unavailable";
}

function costValue(value: number | null | undefined): string {
  return typeof value === "number" ? `$${value.toFixed(4)}` : "Unavailable";
}

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
  const [answer, setAnswer] = useState<AskResponse | null>(null);
  const [promptError, setPromptError] = useState<string | null>(null);
  const [isRunning, setIsRunning] = useState(false);

  // Multi-turn and SSE Inspection State
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [chatTurns, setChatTurns] = useState<ChatTurn[]>([]);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [workingContext, setWorkingContext] = useState<WorkingContextData | null>(null);
  const [runTelemetry, setRunTelemetry] = useState<RunTelemetry | null>(null);
  const [activeTab, setActiveTab] = useState<"timeline" | "context">("timeline");
  const hydrationVersion = useRef(0);

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

  const hydrateConversation = useCallback(async (
    id: string,
    selectedRunId?: string,
    clearPointerOnFailure = false,
  ) => {
    const version = ++hydrationVersion.current;
    try {
      const response = await fetch(`/api/conversations/${encodeURIComponent(id)}`);
      if (!response.ok) throw new Error("Conversation reload failed");
      const snapshot = (await response.json()) as ConversationSnapshot;
      if (hydrationVersion.current !== version) return;
      const orderedMessages = [...snapshot.messages].sort((a, b) => a.sequence - b.sequence);
      const runsByMessageId = new Map(
        snapshot.runs.filter((run) => run.message_id).map((run) => [run.message_id as string, run]),
      );
      setConversationId(snapshot.conversation_id);
      setChatTurns(
        orderedMessages.map((message, index) => {
          const priorMessage = orderedMessages[index - 1];
          const associatedRun = message.role === "assistant" && priorMessage?.role === "user"
            ? runsByMessageId.get(priorMessage.message_id)
            : undefined;
          return {
            id: message.message_id,
            role: message.role,
            content: message.content,
            timestamp: new Date(message.created_at).toLocaleTimeString(),
            runId: associatedRun?.run_id,
            tokens: associatedRun ? associatedRun.input_tokens + associatedRun.output_tokens : undefined,
          };
        }),
      );
      const activeRun = selectedRunId ?? snapshot.runs.at(-1)?.run_id ?? null;
      setActiveRunId(activeRun);
    } catch {
      if (clearPointerOnFailure && hydrationVersion.current === version) {
        window.localStorage.removeItem(CONVERSATION_STORAGE_KEY);
      }
    }
  }, []);

  useEffect(() => {
    const savedConversationId = window.localStorage.getItem(CONVERSATION_STORAGE_KEY);
    if (savedConversationId) void hydrateConversation(savedConversationId, undefined, true);
  }, [hydrateConversation]);

  const submitPrompt = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const cleanPrompt = prompt.trim();
    if (!cleanPrompt || isRunning) return;

    hydrationVersion.current += 1;
    setIsRunning(true);
    setAnswer(null);
    setPromptError(null);
    setWorkingContext(null);
    setRunTelemetry(null);

    try {
      const response = await fetch("/api/ask", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(
          conversationId
            ? { prompt: cleanPrompt, conversation_id: conversationId }
            : { prompt: cleanPrompt },
        ),
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
      setConversationId(askResp.conversation_id);
      window.localStorage.setItem(CONVERSATION_STORAGE_KEY, askResp.conversation_id);
      setActiveRunId(askResp.run_id);
      await hydrateConversation(askResp.conversation_id, askResp.run_id);

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
    hydrationVersion.current += 1;
    setActiveRunId(runId);
    setWorkingContext(null);
    setRunTelemetry(null);
  };

  const handleWorkingContextUpdate = (runId: string, ctx: WorkingContextData) => {
    if (runId === activeRunId) setWorkingContext(ctx);
  };

  const handleRunTelemetryUpdate = (runId: string, telemetry: RunTelemetry) => {
    if (runId === activeRunId) setRunTelemetry(telemetry);
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
          {conversationId && (
            <div className="conversation-identity" aria-label="Conversation and current run identity">
              <span>Conversation: <code>{conversationId}</code></span>
              {activeRunId && <span>Current Run: <code>{activeRunId}</code></span>}
            </div>
          )}
          {/* Multi-turn Archived Chat History */}
          {chatTurns.length > 0 && (
            <div className="chat-history-container" aria-label="Conversation turns">
              <div className="conversation-header-row">
                <span className="card-label">Prior Turns Thread: <code>{conversationId}</code></span>
                <span className="turns-counter">{chatTurns.filter((turn) => turn.role === "user").length} prior turns</span>
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
                            onClick={() => turn.runId && handleInspectRun(turn.runId)}
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
                    hydrationVersion.current += 1;
                    setConversationId(null);
                    window.localStorage.removeItem(CONVERSATION_STORAGE_KEY);
                    setChatTurns([]);
                    setWorkingContext(null);
                    setRunTelemetry(null);
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
              {runTelemetry && (
                <dl className="run-telemetry" aria-label="Authoritative run telemetry">
                  <div><dt>End-to-end latency</dt><dd>{metricValue(runTelemetry.end_to_end_latency_ms, " ms")}</dd></div>
                  <div><dt>LLM proposal latency</dt><dd>{metricValue(runTelemetry.proposal_llm_latency_ms, " ms")}</dd></div>
                  <div><dt>MCP/tool latency</dt><dd>{metricValue(runTelemetry.tool_latency_ms, " ms")}</dd></div>
                  <div><dt>LLM final-answer latency</dt><dd>{metricValue(runTelemetry.final_answer_llm_latency_ms, " ms")}</dd></div>
                  <div><dt>Total input tokens</dt><dd>{metricValue(runTelemetry.input_tokens)}</dd></div>
                  <div><dt>Total output tokens</dt><dd>{metricValue(runTelemetry.output_tokens)}</dd></div>
                  <div><dt>Estimated cost</dt><dd>{costValue(runTelemetry.estimated_cost_usd)}</dd></div>
                  <div><dt>TTFT</dt><dd>{runTelemetry.ttft?.available ? "Available" : "Unavailable (non-streaming)"}</dd></div>
                </dl>
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
                onRunTelemetryUpdate={handleRunTelemetryUpdate}
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
