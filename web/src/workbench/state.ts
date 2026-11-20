import {
  parseArtifactEnvelope,
  type ArtifactEnvelope,
} from "../artifacts/envelope";

export const AGENT_EVENT_SCHEMA_VERSION = "agent_event.v1" as const;

export type AgentEventType =
  | "run_started"
  | "agent_spawned"
  | "agent_started"
  | "agent_progress"
  | "tool_started"
  | "tool_completed"
  | "tool_failed"
  | "artifact_created"
  | "agent_completed"
  | "agent_failed"
  | "run_cancelled"
  | "run_completed"
  | "run_failed";

export interface MeasuredUsage {
  input_tokens: number | null;
  output_tokens: number | null;
  latency_ms: number | null;
  cost_usd: number | null;
}

export interface AgentStreamEvent {
  schema_version: typeof AGENT_EVENT_SCHEMA_VERSION;
  sequence: number;
  timestamp: string;
  trace_id: string;
  run_id: string;
  tenant_id: string;
  agent_id: string;
  parent_agent_id: string | null;
  event_type: AgentEventType;
  tool_call_id?: string | null;
  artifact_id?: string | null;
  payload: Record<string, unknown>;
  usage?: MeasuredUsage | null;
}

export type AgentStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export interface ToolCallTrace {
  toolCallId: string;
  toolName: string;
  status: "running" | "completed" | "failed";
  detail: string;
}

export interface AgentCardState {
  agentId: string;
  role: string;
  parentAgentId: string | null;
  status: AgentStatus;
  summary: string;
  toolCalls: ToolCallTrace[];
  artifactIds: string[];
  usage: MeasuredUsage | null;
  errorMessage: string | null;
}

export interface TimelineItem {
  sequence: number;
  timestamp: string;
  agentId: string;
  eventType: AgentEventType;
  summary: string;
  toolCallId: string | null;
  artifactId: string | null;
}

export interface WorkbenchState {
  runId: string | null;
  lastSequence: number;
  seenSequences: Record<number, true>;
  agentOrder: string[];
  agents: Record<string, AgentCardState>;
  timeline: TimelineItem[];
  artifacts: Record<string, ArtifactEnvelope>;
  artifactOrder: string[];
  finalStatus: "idle" | "running" | "completed" | "failed" | "cancelled";
}

export function createInitialWorkbenchState(): WorkbenchState {
  return {
    runId: null,
    lastSequence: 0,
    seenSequences: {},
    agentOrder: [],
    agents: {},
    timeline: [],
    artifacts: {},
    artifactOrder: [],
    finalStatus: "idle",
  };
}

function summarizeEvent(ev: AgentStreamEvent): string {
  const p = ev.payload;
  if (typeof p.message === "string" && p.message.trim()) return p.message;
  if (typeof p.summary === "string" && p.summary.trim()) return p.summary;
  if (typeof p.error === "string" && p.error.trim()) return p.error;
  if (typeof p.tool_name === "string") {
    return `${ev.event_type}: ${p.tool_name}`;
  }
  if (typeof p.title === "string") return `${ev.event_type}: ${p.title}`;
  return ev.event_type;
}

export function applyAgentEvent(
  state: WorkbenchState,
  ev: AgentStreamEvent,
): WorkbenchState {
  if (ev.schema_version !== AGENT_EVENT_SCHEMA_VERSION) return state;
  if (state.seenSequences[ev.sequence]) return state;

  const seenSequences = { ...state.seenSequences, [ev.sequence]: true as const };
  const lastSequence = Math.max(state.lastSequence, ev.sequence);
  const runId = state.runId ?? ev.run_id;

  const agents = { ...state.agents };
  let agentOrder = state.agentOrder;

  const ensureAgent = (
    agentId: string,
    parentAgentId: string | null,
    role?: string,
  ): AgentCardState => {
    const existing = agents[agentId];
    if (existing) {
      return {
        ...existing,
        toolCalls: [...existing.toolCalls],
        artifactIds: [...existing.artifactIds],
      };
    }
    agentOrder = [...agentOrder, agentId];
    return {
      agentId,
      role: role ?? (agentId === "coordinator" ? "Coordinator" : agentId),
      parentAgentId,
      status: "queued",
      summary: "",
      toolCalls: [],
      artifactIds: [],
      usage: null,
      errorMessage: null,
    };
  };

  let finalStatus = state.finalStatus;
  const artifacts = { ...state.artifacts };
  let artifactOrder = state.artifactOrder;

  const role =
    typeof ev.payload.role === "string" ? ev.payload.role : undefined;
  const card = ensureAgent(ev.agent_id, ev.parent_agent_id, role);

  switch (ev.event_type) {
    case "run_started":
      finalStatus = "running";
      card.status = "running";
      card.summary = summarizeEvent(ev);
      break;
    case "agent_spawned":
      card.status = "queued";
      card.summary = summarizeEvent(ev);
      break;
    case "agent_started":
      card.status = "running";
      card.summary = summarizeEvent(ev);
      break;
    case "agent_progress":
      if (card.status === "queued") card.status = "running";
      card.summary = summarizeEvent(ev);
      break;
    case "tool_started": {
      card.status = "running";
      const tcId = ev.tool_call_id ?? `tc_${ev.sequence}`;
      const toolName =
        typeof ev.payload.tool_name === "string" ? ev.payload.tool_name : "tool";
      card.toolCalls.push({
        toolCallId: tcId,
        toolName,
        status: "running",
        detail: summarizeEvent(ev),
      });
      break;
    }
    case "tool_completed": {
      const tcId = ev.tool_call_id ?? "";
      const idx = card.toolCalls.findIndex((t) => t.toolCallId === tcId);
      const detail = summarizeEvent(ev);
      if (idx >= 0) {
        card.toolCalls[idx] = { ...card.toolCalls[idx], status: "completed", detail };
      } else {
        card.toolCalls.push({
          toolCallId: tcId || `tc_${ev.sequence}`,
          toolName:
            typeof ev.payload.tool_name === "string" ? ev.payload.tool_name : "tool",
          status: "completed",
          detail,
        });
      }
      break;
    }
    case "tool_failed": {
      const tcId = ev.tool_call_id ?? "";
      const idx = card.toolCalls.findIndex((t) => t.toolCallId === tcId);
      const detail = summarizeEvent(ev);
      if (idx >= 0) {
        card.toolCalls[idx] = { ...card.toolCalls[idx], status: "failed", detail };
      } else {
        card.toolCalls.push({
          toolCallId: tcId || `tc_${ev.sequence}`,
          toolName:
            typeof ev.payload.tool_name === "string" ? ev.payload.tool_name : "tool",
          status: "failed",
          detail,
        });
      }
      break;
    }
    case "artifact_created": {
      const aid = ev.artifact_id ?? `art_${ev.sequence}`;
      if (!card.artifactIds.includes(aid)) card.artifactIds.push(aid);
      if (ev.payload.envelope) {
        const parsed = parseArtifactEnvelope(ev.payload.envelope);
        if (parsed.ok) {
          artifacts[parsed.envelope.artifact_id] = parsed.envelope;
          if (!artifactOrder.includes(parsed.envelope.artifact_id)) {
            artifactOrder = [...artifactOrder, parsed.envelope.artifact_id];
          }
        }
      }
      break;
    }
    case "agent_completed":
      card.status = "completed";
      card.summary = summarizeEvent(ev);
      if (ev.usage) card.usage = ev.usage;
      break;
    case "agent_failed":
      card.status = "failed";
      card.errorMessage = summarizeEvent(ev);
      if (ev.usage) card.usage = ev.usage;
      break;
    case "run_cancelled":
      finalStatus = "cancelled";
      card.status = "cancelled";
      card.summary = summarizeEvent(ev);
      for (const id of Object.keys(agents)) {
        if (agents[id].status === "queued" || agents[id].status === "running") {
          agents[id] = { ...agents[id], status: "cancelled" };
        }
      }
      break;
    case "run_completed":
      finalStatus = "completed";
      card.status = "completed";
      card.summary = summarizeEvent(ev);
      if (ev.usage) card.usage = ev.usage;
      break;
    case "run_failed":
      finalStatus = "failed";
      card.status = "failed";
      card.errorMessage = summarizeEvent(ev);
      break;
  }

  agents[ev.agent_id] = card;

  const timeline = [
    ...state.timeline,
    {
      sequence: ev.sequence,
      timestamp: ev.timestamp,
      agentId: ev.agent_id,
      eventType: ev.event_type,
      summary: summarizeEvent(ev),
      toolCallId: ev.tool_call_id ?? null,
      artifactId: ev.artifact_id ?? null,
    },
  ].sort((a, b) => a.sequence - b.sequence);

  return {
    runId,
    lastSequence,
    seenSequences,
    agentOrder,
    agents,
    timeline,
    artifacts,
    artifactOrder,
    finalStatus,
  };
}

export function applyAgentEvents(
  state: WorkbenchState,
  events: readonly AgentStreamEvent[],
): WorkbenchState {
  return events.reduce(applyAgentEvent, state);
}
