import { useMemo, useState } from "react";
import { synthesizeArtifacts } from "../artifacts/envelope";
import { ArtifactPanel, SynthesisBanner } from "../artifacts/ArtifactPanel";
import {
  applyAgentEvent,
  applyAgentEvents,
  createInitialWorkbenchState,
  type AgentCardState,
  type AgentStreamEvent,
  type WorkbenchState,
} from "./state";

interface SpecialistWorkbenchProps {
  title: string;
  question: string;
  events: readonly AgentStreamEvent[];
  baselineEvents?: readonly AgentStreamEvent[];
  initialStep?: number;
}

function formatUsage(card: AgentCardState): string {
  const u = card.usage;
  if (!u) return "Usage: Unavailable";
  const tokens =
    u.input_tokens != null && u.output_tokens != null
      ? `${u.input_tokens} in / ${u.output_tokens} out`
      : "tokens n/a";
  const latency = u.latency_ms != null ? `${u.latency_ms} ms` : "latency n/a";
  const cost = u.cost_usd != null ? `$${u.cost_usd.toFixed(4)}` : "cost n/a";
  return `${tokens} · ${latency} · ${cost}`;
}

export function SpecialistWorkbench({
  title,
  question,
  events,
  baselineEvents,
  initialStep,
}: SpecialistWorkbenchProps) {
  const [mode, setMode] = useState<"specialist" | "baseline">("specialist");
  const activeStream = mode === "baseline" && baselineEvents ? baselineEvents : events;

  const [cursor, setCursor] = useState<number>(initialStep ?? activeStream.length);
  const [extraEvents, setExtraEvents] = useState<AgentStreamEvent[]>([]);
  const [reconnectNotice, setReconnectNotice] = useState<string | null>(null);
  const [zoneFollowUp, setZoneFollowUp] = useState<string | null>(null);
  const [expandedAgents, setExpandedAgents] = useState<Record<string, boolean>>({});

  const switchMode = (next: "specialist" | "baseline") => {
    setMode(next);
    const target = next === "baseline" && baselineEvents ? baselineEvents : events;
    setCursor(target.length);
    setExtraEvents([]);
    setReconnectNotice(null);
  };

  const state: WorkbenchState = useMemo(() => {
    const base = applyAgentEvents(
      createInitialWorkbenchState(),
      activeStream.slice(0, cursor),
    );
    return applyAgentEvents(base, extraEvents);
  }, [activeStream, cursor, extraEvents]);

  const artifactList = useMemo(
    () => state.artifactOrder.map((id) => state.artifacts[id]).filter(Boolean),
    [state.artifactOrder, state.artifacts],
  );
  const synthesisReport = useMemo(
    () => synthesizeArtifacts(artifactList),
    [artifactList],
  );

  const handleStep = () => {
    if (cursor < activeStream.length) setCursor((c) => c + 1);
  };

  const handleComplete = () => {
    setCursor(activeStream.length);
  };

  const handleReset = () => {
    setCursor(0);
    setExtraEvents([]);
    setReconnectNotice(null);
  };

  const handleSimulateReconnect = () => {
    // Re-deliver overlapping frames (1..cursor) to prove dedupe produces zero duplicate cards/rows.
    const replay = activeStream.slice(0, Math.min(cursor, activeStream.length));
    const reapplied = applyAgentEvents(state, replay);
    setReconnectNotice(
      `Replayed ${replay.length} overlapping frames through lastSequence=${reapplied.lastSequence}; ${reapplied.agentOrder.length} unique agents, ${reapplied.timeline.length} unique timeline events.`,
    );
  };

  const handleCancel = () => {
    if (state.finalStatus === "completed" || state.finalStatus === "cancelled") return;
    const cancelEv: AgentStreamEvent = {
      schema_version: "agent_event.v1",
      sequence: state.lastSequence + 1,
      timestamp: new Date().toISOString(),
      trace_id: activeStream[0]?.trace_id ?? "trace_cancel",
      run_id: state.runId ?? "run_cancel",
      tenant_id: activeStream[0]?.tenant_id ?? "tenant_demo",
      agent_id: "coordinator",
      parent_agent_id: null,
      event_type: "run_cancelled",
      payload: { message: "Run cancelled by user" },
    };
    const next = applyAgentEvent(state, cancelEv);
    if (next !== state) {
      setExtraEvents((prev) => [...prev, cancelEv]);
    }
  };

  const toggleExpand = (agentId: string) => {
    setExpandedAgents((prev) => ({ ...prev, [agentId]: !prev[agentId] }));
  };

  return (
    <div className="workbench-shell" style={{ maxWidth: 1120, margin: "0 auto", padding: 20 }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16, flexWrap: "wrap" }}>
        <div>
          <h2 style={{ margin: "0 0 4px" }}>{title}</h2>
          <p style={{ margin: 0, color: "#475569" }}>{question}</p>
        </div>
        <div role="status" aria-label="Run status" data-run-status={state.finalStatus}>
          <strong>Status:</strong> {state.finalStatus} · seq {state.lastSequence}/
          {activeStream.length}
        </div>
      </header>

      <div
        role="toolbar"
        aria-label="Workbench stream controls"
        style={{ display: "flex", gap: 8, flexWrap: "wrap", margin: "16px 0" }}
      >
        <button type="button" onClick={handleReset}>
          Reset
        </button>
        <button type="button" onClick={handleStep} disabled={cursor >= activeStream.length}>
          Next event ({cursor}/{activeStream.length})
        </button>
        <button type="button" onClick={handleComplete} disabled={cursor >= activeStream.length}>
          Stream all
        </button>
        <button type="button" onClick={handleSimulateReconnect} disabled={cursor === 0}>
          Simulate reconnect
        </button>
        <button
          type="button"
          onClick={handleCancel}
          disabled={state.finalStatus === "completed" || state.finalStatus === "cancelled"}
        >
          Cancel run
        </button>
        {baselineEvents && (
          <button
            type="button"
            onClick={() => switchMode(mode === "specialist" ? "baseline" : "specialist")}
          >
            {mode === "specialist"
              ? "Compare: Single-Agent Baseline"
              : "Back to Specialist Workbench"}
          </button>
        )}
      </div>

      {reconnectNotice && (
        <p role="status" style={{ background: "#f1f5f9", padding: "8px 12px", borderRadius: 6 }}>
          {reconnectNotice}
        </p>
      )}

      <section aria-label="Active agents">
        <h3>Agents ({state.agentOrder.length})</h3>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
            gap: 12,
          }}
        >
          {state.agentOrder.map((agentId) => {
            const card = state.agents[agentId];
            const isExpanded = Boolean(expandedAgents[agentId]);
            return (
              <article
                key={card.agentId}
                data-agent-card={card.agentId}
                data-agent-status={card.status}
                style={{
                  border: "1px solid #cbd5e1",
                  borderRadius: 8,
                  padding: 12,
                  background: "#ffffff",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                  <strong>{card.role}</strong>
                  <span data-status-badge={card.status}>{card.status}</span>
                </div>
                <p style={{ fontSize: 12, color: "#64748b", margin: "4px 0" }}>
                  id: <code>{card.agentId}</code>
                  {card.parentAgentId ? ` · parent: ${card.parentAgentId}` : " · root"}
                </p>
                <p style={{ margin: "6px 0" }}>{card.errorMessage ?? card.summary}</p>
                <p style={{ fontSize: 12, color: "#475569", margin: "4px 0" }}>
                  {formatUsage(card)}
                </p>

                {card.toolCalls.length > 0 && (
                  <div>
                    <button
                      type="button"
                      aria-expanded={isExpanded}
                      onClick={() => toggleExpand(card.agentId)}
                    >
                      {isExpanded ? "Hide" : "Show"} tool trace ({card.toolCalls.length})
                    </button>
                    {isExpanded && (
                      <ul style={{ fontSize: 13, paddingLeft: 18 }}>
                        {card.toolCalls.map((tc) => (
                          <li key={tc.toolCallId} data-tool-status={tc.status}>
                            <code>{tc.toolName}</code> [{tc.status}]: {tc.detail}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}
              </article>
            );
          })}
        </div>
      </section>

      {artifactList.length > 0 && (
        <section aria-label="Synthesized artifacts" style={{ marginTop: 20 }}>
          <h3>Evidence &amp; Artifacts ({artifactList.length})</h3>
          <SynthesisBanner report={synthesisReport} />
          {zoneFollowUp && (
            <p role="status" style={{ marginTop: 8 }}>
              Drill-down prompt: <strong>{zoneFollowUp}</strong>
            </p>
          )}
          <div style={{ display: "grid", gap: 16, marginTop: 12 }}>
            {artifactList.map((env) => (
              <ArtifactPanel
                key={env.artifact_id}
                rawEnvelope={env}
                onAskAboutZone={(zone) =>
                  setZoneFollowUp(`Drill down on ${zone} pickups and fares`)
                }
              />
            ))}
          </div>
        </section>
      )}

      <section aria-label="Event timeline" style={{ marginTop: 20 }}>
        <h3>Event Timeline ({state.timeline.length})</h3>
        <ol style={{ fontSize: 13 }}>
          {state.timeline.map((item) => (
            <li key={item.sequence} data-timeline-seq={item.sequence}>
              <code>#{item.sequence}</code> [{item.agentId}] <strong>{item.eventType}</strong> —{" "}
              {item.summary}
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
