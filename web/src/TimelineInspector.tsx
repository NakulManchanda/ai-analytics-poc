import React, { useEffect, useRef, useState } from "react";
import { RunEvent, RunTelemetry, WorkingContextData } from "./types";

interface TimelineInspectorProps {
  runId: string | null;
  onWorkingContextUpdate?: (runId: string, context: WorkingContextData) => void;
  onRunTelemetryUpdate?: (runId: string, telemetry: RunTelemetry) => void;
  onInspectRun?: (runId: string) => void;
}

function terminalTelemetry(event: RunEvent): RunTelemetry | null {
  if (!['run.completed', 'run.budget_exceeded', 'run.failed', 'run.cancelled'].includes(event.event_type)) return null;
  const payload = event.payload;
  const telemetry = payload.telemetry as Record<string, unknown> | undefined;
  const value = (key: string) => payload[key] ?? telemetry?.[key];
  const numericValue = (key: string) => {
    const candidate = value(key);
    return typeof candidate === 'number' ? candidate : undefined;
  };
  const ttft = value('ttft');
  return {
    input_tokens: numericValue('input_tokens'),
    output_tokens: numericValue('output_tokens'),
    total_tokens: numericValue('total_tokens'),
    estimated_cost_usd: numericValue('estimated_cost_usd'),
    end_to_end_latency_ms: numericValue('end_to_end_latency_ms'),
    proposal_llm_latency_ms: numericValue('proposal_llm_latency_ms'),
    tool_latency_ms: numericValue('tool_latency_ms'),
    final_answer_llm_latency_ms: numericValue('final_answer_llm_latency_ms'),
    ttft: typeof ttft === 'object' && ttft !== null && 'available' in ttft
      ? ttft as RunTelemetry['ttft']
      : undefined,
  };
}

export const KNOWN_EVENT_TYPES = [
  "message",
  "run.received",
  "run.cancel_requested",
  "run.cancelled",
  "context.loading",
  "llm.started",
  "llm.completed",
  "tool.requested",
  "tool.started",
  "tool.completed",
  "tool.failed",
  "context.reduced",
  "run.completed",
  "run.budget_exceeded",
  "run.failed",
  "step.tool_proposal",
  "step.tool_execution",
  "step.final_answer",
  "step.llm_proposal",
  "step.tool_call",
  "step.llm_final_answer",
  "step.validation_error",
  "job.completed",
  "job.failed",
];

export function getEventBadgeClass(eventType: string): string {
  if (eventType === "run.cancel_requested" || eventType === "run.cancelled") return "badge-warning";
  if (eventType === "tool.failed" || eventType === "run.budget_exceeded" || eventType === "run.failed" || eventType === "job.failed") return "badge-danger";
  if (eventType.startsWith("llm.") || eventType === "step.llm_proposal" || eventType === "step.tool_proposal" || eventType === "step.final_answer" || eventType === "step.llm_final_answer") return "badge-llm";
  if (eventType.startsWith("tool.") || eventType === "step.tool_execution" || eventType === "step.tool_call") return "badge-tool";
  if (eventType.startsWith("context.")) return "badge-context";
  if (eventType === "run.completed" || eventType === "job.completed") return "badge-success";
  return "badge-default";
}

export function formatEventSummary(event: RunEvent): string {
  const p = event.payload || {};
  switch (event.event_type) {
    case "run.received":
      return p.prompt_summary ? `Prompt: "${p.prompt_summary}"` : "Execution run initialized";
    case "run.cancel_requested":
      return "Cancellation requested by user";
    case "run.cancelled":
      return `Run cancelled · ${p.total_tokens ?? (Number(p.input_tokens || 0) + Number(p.output_tokens || 0))} tokens consumed · ${p.latency_ms ?? 0}ms`;
    case "context.loading":
      return `Loading schema: ${p.resource || "dataset schema"}`;
    case "llm.started":
      return `Model call started (${p.phase || "generation"})`;
    case "llm.completed":
      return `Model completed (${p.phase || "generation"}) · ${p.latency_ms ?? 0}ms · ${p.tokens?.input ?? 0} in / ${p.tokens?.output ?? 0} out`;
    case "tool.requested":
    case "step.tool_proposal":
    case "step.llm_proposal":
      return `Tool proposal: ${p.tool_name || "query_taxi_data"} (${p.analysis || p.input_summary || "default"}, limit=${p.limit ?? 5})`;
    case "tool.started":
      return `Executing ${p.tool_name || "MCP tool"} in DuckDB`;
    case "tool.completed":
    case "step.tool_execution":
    case "step.tool_call":
      return `DuckDB query ${p.query_id || ""} completed · ${p.row_count ?? p.output_summary ?? 0} rows · ${p.duration_ms ?? 0}ms`;
    case "tool.failed":
      return `Tool ${p.tool_name || "execution"} failed: ${p.error || "execution error"}`;
    case "step.final_answer":
    case "step.llm_final_answer":
      return `Final answer synthesized · ${p.output_summary ? `"${p.output_summary.slice(0, 80)}..."` : "response ready"}`;
    case "context.reduced":
      return `Working context reduced · query ${p.query_id || ""} rows bounded to working window`;
    case "run.completed":
    case "job.completed":
      return `Run completed successfully · ${p.total_tokens ?? (Number(p.input_tokens || 0) + Number(p.output_tokens || 0))} tokens · $${Number(p.estimated_cost_usd ?? 0.001).toFixed(4)} · ${p.latency_ms ?? 0}ms`;
    case "run.budget_exceeded":
      return `Budget exceeded: ${p.reason || p.failure_code || "limit breached"}`;
    case "run.failed":
    case "job.failed":
      return `Run failed: ${p.error ? `${p.failure_code ? `[${p.failure_code}] ` : ""}${p.error}` : p.failure_code || "error"}`;
    default:
      if (event.event_type.startsWith("step.")) {
        return `${p.input_summary || ""} -> ${p.output_summary || ""}`.trim() || event.event_type;
      }
      return JSON.stringify(p);
  }
}

export const TimelineInspector: React.FC<TimelineInspectorProps> = ({
  runId,
  onWorkingContextUpdate,
  onRunTelemetryUpdate,
  onInspectRun,
}) => {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const latestWorkingContextUpdate = useRef(onWorkingContextUpdate);
  const latestRunTelemetryUpdate = useRef(onRunTelemetryUpdate);
  const [connectionStatus, setConnectionStatus] = useState<
    "idle" | "connecting" | "streaming" | "completed" | "budget_exceeded" | "failed" | "cancelled" | "cancelling" | "error"
  >("idle");
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [customRunInput, setCustomRunInput] = useState("");

  latestWorkingContextUpdate.current = onWorkingContextUpdate;
  latestRunTelemetryUpdate.current = onRunTelemetryUpdate;

  useEffect(() => {
    if (!runId) {
      setEvents([]);
      setConnectionStatus("idle");
      return;
    }

    setEvents([]);
    setConnectionStatus("connecting");
    let active = true;

    // Check if EventSource is available in environment
    if (typeof EventSource !== "undefined") {
      const eventSource = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`);

      eventSource.onopen = () => {
        if (active) setConnectionStatus("streaming");
      };

      const handleRawEvent = (rawEvt: MessageEvent) => {
        if (!active) return;
        try {
          const parsed = JSON.parse(rawEvt.data) as RunEvent;
          setEvents((prev) => {
            if (prev.some((e) => e.event_id === parsed.event_id)) return prev;
            return [...prev, parsed].sort((a, b) => a.sequence - b.sequence);
          });

          // Check if payload has working_context
          if (parsed.payload?.working_context && latestWorkingContextUpdate.current) {
            latestWorkingContextUpdate.current(runId, parsed.payload.working_context);
          }
          const telemetry = terminalTelemetry(parsed);
          if (telemetry && latestRunTelemetryUpdate.current) {
            latestRunTelemetryUpdate.current(runId, telemetry);
          }

          // Check terminal events
          if (parsed.event_type === "run.completed") {
            setConnectionStatus("completed");
            eventSource.close();
          } else if (parsed.event_type === "run.budget_exceeded") {
            setConnectionStatus("budget_exceeded");
            eventSource.close();
          } else if (parsed.event_type === "run.failed") {
            setConnectionStatus("failed");
            eventSource.close();
          } else if (parsed.event_type === "run.cancelled") {
            setConnectionStatus("cancelled");
            eventSource.close();
          } else if (parsed.event_type === "run.cancel_requested") {
            setConnectionStatus("cancelling");
          }
        } catch {
          // ignore keep-alives or non-json comments
        }
      };

      KNOWN_EVENT_TYPES.forEach((type) => {
        eventSource.addEventListener(type, handleRawEvent);
      });

      eventSource.onerror = () => {
        if (!active) return;
        // If we already have terminal events, stay completed
        setEvents((currentEvents) => {
          const hasTerminal = currentEvents.some((e) =>
            ["run.completed", "run.budget_exceeded", "run.failed", "run.cancelled"].includes(e.event_type)
          );
          if (!hasTerminal) {
            setConnectionStatus("error");
          }
          return currentEvents;
        });
        eventSource.close();
      };

      return () => {
        active = false;
        eventSource.close();
      };
    } else {
      // Fallback for test/environments without EventSource (using fetch)
      const controller = new AbortController();

      const fetchStream = async () => {
        try {
          const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/events`, {
            signal: controller.signal,
          });
          if (!res.ok) {
            if (active) setConnectionStatus("error");
            return;
          }
          if (active) setConnectionStatus("streaming");

          const text =
            typeof res.text === "function"
              ? await res.text()
              : typeof res.json === "function"
                ? JSON.stringify(await res.json())
                : "";
          if (!active) return;

          // Parse SSE text format
          const rawLines = text.split("\n");
          const incomingEvents: RunEvent[] = [];
          for (let i = 0; i < rawLines.length; i++) {
            const line = rawLines[i].trim();
            if (line.startsWith("data: ")) {
              try {
                const dataJson = JSON.parse(line.substring(6)) as RunEvent;
                incomingEvents.push(dataJson);
                if (dataJson.payload?.working_context && latestWorkingContextUpdate.current) {
                  latestWorkingContextUpdate.current(runId, dataJson.payload.working_context);
                }
                const telemetry = terminalTelemetry(dataJson);
                if (telemetry && latestRunTelemetryUpdate.current) {
                  latestRunTelemetryUpdate.current(runId, telemetry);
                }
              } catch {
                // ignore
              }
            }
          }

          if (incomingEvents.length > 0) {
            setEvents(incomingEvents.sort((a, b) => a.sequence - b.sequence));
            const lastEvt = incomingEvents[incomingEvents.length - 1];
            if (lastEvt.event_type === "run.completed") setConnectionStatus("completed");
            else if (lastEvt.event_type === "run.budget_exceeded") setConnectionStatus("budget_exceeded");
            else if (lastEvt.event_type === "run.failed") setConnectionStatus("failed");
            else if (lastEvt.event_type === "run.cancelled") setConnectionStatus("cancelled");
            else if (lastEvt.event_type === "run.cancel_requested") setConnectionStatus("cancelling");
            else setConnectionStatus("streaming");
          }
        } catch {
          if (active) setConnectionStatus("error");
        }
      };

      void fetchStream();

      return () => {
        active = false;
        controller.abort();
      };
    }
  }, [runId]);

  const handleInspectSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (customRunInput.trim() && onInspectRun) {
      onInspectRun(customRunInput.trim());
    }
  };

  const selectedEvent = events.find((e) => e.event_id === selectedEventId);

  return (
    <aside className="timeline-panel" aria-labelledby="timeline-inspector-heading">
      <div className="timeline-header">
        <div>
          <p className="card-label">Real-time Telemetry</p>
          <h2 id="timeline-inspector-heading">Run Timeline Inspector</h2>
        </div>
        <div className="status-indicator-group">
          <span className={`status-pill ${connectionStatus}`}>
            <span className="pulse-dot" aria-hidden="true" />
            {connectionStatus.toUpperCase().replace("_", " ")}
          </span>
        </div>
      </div>

      {/* Direct Run ID Inspector Input */}
      <form className="run-id-inspector-form" onSubmit={handleInspectSubmit}>
        <label htmlFor="run-id-input" className="sr-only">Run ID</label>
        <input
          id="run-id-input"
          type="text"
          value={customRunInput}
          onChange={(e) => setCustomRunInput(e.target.value)}
          placeholder={runId ? `Active: ${runId}` : "Inspect run ID (e.g. run_...)"}
          className="run-id-input"
        />
        <button type="submit" className="btn-secondary" disabled={!customRunInput.trim()}>
          Connect SSE
        </button>
      </form>

      {runId && (
        <div className="active-run-meta">
          <span>Connected to SSE Stream: <code>/api/runs/{runId}/events</code></span>
        </div>
      )}

      {/* Events Timeline List */}
      {events.length === 0 ? (
        <div className="timeline-empty">
          <p>
            {runId
              ? "Connecting to execution event stream…"
              : "No run active. Ask a question or enter a Run ID above to stream live events."}
          </p>
        </div>
      ) : (
        <ol className="timeline-events-list" aria-label="Execution events sequence">
          {events.map((evt) => {
            const isSelected = evt.event_id === selectedEventId;
            return (
              <li
                key={evt.event_id || evt.sequence}
                className={`timeline-event-item ${isSelected ? "selected" : ""}`}
                onClick={() => setSelectedEventId(isSelected ? null : evt.event_id)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    setSelectedEventId(isSelected ? null : evt.event_id);
                  }
                }}
                aria-expanded={isSelected}
              >
                <div className="event-sequence-col">
                  <span className="seq-number">#{evt.sequence}</span>
                </div>
                <div className="event-content-col">
                  <div className="event-title-row">
                    <span className={`event-badge ${getEventBadgeClass(evt.event_type)}`}>
                      {evt.event_type}
                    </span>
                    {evt.step_id && <span className="step-id-tag">{evt.step_id}</span>}
                  </div>
                  <p className="event-summary-text">{formatEventSummary(evt)}</p>

                  {/* Expanded Event Payload Detail */}
                  {isSelected && (
                    <div className="event-detail-drawer" onClick={(e) => e.stopPropagation()}>
                      <div className="drawer-meta-grid">
                        <div>
                          <span className="card-label">Event ID:</span>
                          <code>{evt.event_id}</code>
                        </div>
                        {evt.llm_call_id && (
                          <div>
                            <span className="card-label">LLM Call ID:</span>
                            <code>{evt.llm_call_id}</code>
                          </div>
                        )}
                        {evt.tool_call_id && (
                          <div>
                            <span className="card-label">Tool Call ID:</span>
                            <code>{evt.tool_call_id}</code>
                          </div>
                        )}
                        {evt.query_id && (
                          <div>
                            <span className="card-label">Query ID:</span>
                            <code>{evt.query_id}</code>
                          </div>
                        )}
                      </div>
                      <div className="payload-code-block">
                        <span className="card-label">Event Payload:</span>
                        <pre>{JSON.stringify(evt.payload, null, 2)}</pre>
                      </div>
                    </div>
                  )}
                </div>
              </li>
            );
          })}
        </ol>
      )}

      {selectedEvent && (
        <div className="timeline-inspector-footer">
          <span className="card-label">Selected Event:</span>
          <strong>#{selectedEvent.sequence} · {selectedEvent.event_type}</strong>
        </div>
      )}
    </aside>
  );
};
