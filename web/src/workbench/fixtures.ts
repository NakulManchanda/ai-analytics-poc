import queensMapFixture from "../map/fixtures/pickup-count-choropleth.v1.json";
import {
  ARTIFACT_ENVELOPE_SCHEMA_VERSION,
  type ArtifactEnvelope,
} from "../artifacts/envelope";
import {
  AGENT_EVENT_SCHEMA_VERSION,
  type AgentStreamEvent,
} from "./state";

const BASE_TS = "2026-04-20T14:00:";

function ts(sec: number): string {
  return `${BASE_TS}${String(sec).padStart(2, "0")}Z`;
}

export const QUEENS_MAP_ENVELOPE: ArtifactEnvelope = {
  schema_version: ARTIFACT_ENVELOPE_SCHEMA_VERSION,
  artifact_id: "art_map_queens_pickups",
  kind: "map",
  title: "Queens pickup concentration by taxi zone (Jan 2024)",
  provenance: {
    run_id: "run_workbench_001",
    agent_id: "spatial-analyst",
    tool_call_id: "tc_spatial_1",
    tool_name: "map_taxi_zones",
    query_id: "q_spatial_queens_v1",
    dataset: "nyc-yellow-taxi-2024-01",
    time_window: "2024-01-01..2024-01-31",
    filter_basis: "governed_valid_records",
    truncated: false,
  },
  body: queensMapFixture,
};

export const HOURLY_TREND_ENVELOPE: ArtifactEnvelope = {
  schema_version: ARTIFACT_ENVELOPE_SCHEMA_VERSION,
  artifact_id: "art_ts_hourly_pickups",
  kind: "time_series",
  title: "Queens airport & corridor hourly pickups (Jan 2024)",
  provenance: {
    run_id: "run_workbench_001",
    agent_id: "trend-analyst",
    tool_call_id: "tc_trend_2",
    tool_name: "aggregate_taxi_data",
    query_id: "q_trend_hourly_v1",
    dataset: "nyc-yellow-taxi-2024-01",
    time_window: "2024-01-01..2024-01-31",
    filter_basis: "governed_valid_records",
    truncated: false,
  },
  body: {
    x_label: "pickup_hour_bucket",
    y_label: "trip_count",
    unit: "trips",
    points: [
      { x: "00-05", y: 41200 },
      { x: "06-11", y: 89400 },
      { x: "12-17", y: 124600 },
      { x: "18-23", y: 138800 },
    ],
    total_y: 394000,
  },
};

export const WEEKDAY_WEEKEND_ENVELOPE: ArtifactEnvelope = {
  schema_version: ARTIFACT_ENVELOPE_SCHEMA_VERSION,
  artifact_id: "art_seg_weekpart",
  kind: "segment_comparison",
  title: "Weekday vs Weekend average fare by borough (Jan 2024)",
  provenance: {
    run_id: "run_workbench_001",
    agent_id: "trend-analyst",
    tool_call_id: "tc_trend_3",
    tool_name: "aggregate_taxi_data",
    query_id: "q_trend_weekpart_v1",
    dataset: "nyc-yellow-taxi-2024-01",
    time_window: "2024-01-01..2024-01-31",
    filter_basis: "governed_valid_records",
    truncated: false,
  },
  body: {
    dimension: "pickup_borough",
    metric: "average_fare",
    unit: "USD",
    baseline_label: "Weekday",
    comparison_label: "Weekend",
    rows: [
      {
        segment: "Manhattan",
        baseline: 15.05,
        comparison: 16.55,
        delta: 1.5,
        delta_pct: 9.97,
      },
      {
        segment: "Queens",
        baseline: 48.0,
        comparison: 50.4,
        delta: 2.4,
        delta_pct: 5.0,
      },
    ],
  },
};

export function buildConcurrentSpecialistStream(): AgentStreamEvent[] {
  const common = {
    schema_version: AGENT_EVENT_SCHEMA_VERSION,
    trace_id: "trace_workbench_001",
    run_id: "run_workbench_001",
    tenant_id: "tenant_demo",
  };
  return [
    {
      ...common,
      sequence: 1,
      timestamp: ts(0),
      agent_id: "coordinator",
      parent_agent_id: null,
      event_type: "run_started",
      payload: {
        role: "Coordinator",
        message: "Decomposing question into Spatial and Trend specialists",
      },
    },
    {
      ...common,
      sequence: 2,
      timestamp: ts(1),
      agent_id: "spatial-analyst",
      parent_agent_id: "coordinator",
      event_type: "agent_spawned",
      payload: {
        role: "Spatial Analyst",
        message: "Queued to map Queens pickup concentration by zone",
      },
    },
    {
      ...common,
      sequence: 3,
      timestamp: ts(1),
      agent_id: "trend-analyst",
      parent_agent_id: "coordinator",
      event_type: "agent_spawned",
      payload: {
        role: "Trend Analyst",
        message: "Queued to compare hourly volume and weekday/weekend fares",
      },
    },
    {
      ...common,
      sequence: 4,
      timestamp: ts(2),
      agent_id: "spatial-analyst",
      parent_agent_id: "coordinator",
      event_type: "agent_started",
      payload: { message: "Running zone-level choropleth query" },
    },
    {
      ...common,
      sequence: 5,
      timestamp: ts(2),
      agent_id: "trend-analyst",
      parent_agent_id: "coordinator",
      event_type: "agent_started",
      payload: { message: "Running hourly and week-part aggregations" },
    },
    {
      ...common,
      sequence: 6,
      timestamp: ts(3),
      agent_id: "spatial-analyst",
      parent_agent_id: "coordinator",
      event_type: "tool_started",
      tool_call_id: "tc_spatial_1",
      payload: { tool_name: "map_taxi_zones", message: "Querying Queens zones" },
    },
    {
      ...common,
      sequence: 7,
      timestamp: ts(3),
      agent_id: "trend-analyst",
      parent_agent_id: "coordinator",
      event_type: "tool_started",
      tool_call_id: "tc_trend_1",
      payload: {
        tool_name: "aggregate_taxi_data",
        message: "Attempt 1: hourly pickup buckets",
      },
    },
    {
      ...common,
      sequence: 8,
      timestamp: ts(4),
      agent_id: "trend-analyst",
      parent_agent_id: "coordinator",
      event_type: "tool_failed",
      tool_call_id: "tc_trend_1",
      payload: {
        tool_name: "aggregate_taxi_data",
        error: "Transient query timeout — retrying with narrower projection",
      },
    },
    {
      ...common,
      sequence: 9,
      timestamp: ts(5),
      agent_id: "spatial-analyst",
      parent_agent_id: "coordinator",
      event_type: "tool_completed",
      tool_call_id: "tc_spatial_1",
      payload: {
        tool_name: "map_taxi_zones",
        message: "Returned 6 primary Queens pickup zones",
      },
    },
    {
      ...common,
      sequence: 10,
      timestamp: ts(5),
      agent_id: "spatial-analyst",
      parent_agent_id: "coordinator",
      event_type: "artifact_created",
      tool_call_id: "tc_spatial_1",
      artifact_id: QUEENS_MAP_ENVELOPE.artifact_id,
      payload: {
        title: QUEENS_MAP_ENVELOPE.title,
        envelope: QUEENS_MAP_ENVELOPE,
      },
    },
    {
      ...common,
      sequence: 11,
      timestamp: ts(6),
      agent_id: "trend-analyst",
      parent_agent_id: "coordinator",
      event_type: "tool_started",
      tool_call_id: "tc_trend_2",
      payload: {
        tool_name: "aggregate_taxi_data",
        message: "Retry: hourly pickup buckets (projected columns)",
      },
    },
    {
      ...common,
      sequence: 12,
      timestamp: ts(7),
      agent_id: "spatial-analyst",
      parent_agent_id: "coordinator",
      event_type: "agent_completed",
      payload: { summary: "JFK (154k) and LaGuardia (142k) dominate Queens pickups." },
      usage: { input_tokens: 920, output_tokens: 210, latency_ms: 1420, cost_usd: 0.0031 },
    },
    {
      ...common,
      sequence: 13,
      timestamp: ts(8),
      agent_id: "trend-analyst",
      parent_agent_id: "coordinator",
      event_type: "tool_completed",
      tool_call_id: "tc_trend_2",
      payload: {
        tool_name: "aggregate_taxi_data",
        message: "Hourly buckets and weekday/weekend comparison succeeded",
      },
    },
    {
      ...common,
      sequence: 14,
      timestamp: ts(9),
      agent_id: "trend-analyst",
      parent_agent_id: "coordinator",
      event_type: "artifact_created",
      tool_call_id: "tc_trend_2",
      artifact_id: HOURLY_TREND_ENVELOPE.artifact_id,
      payload: {
        title: HOURLY_TREND_ENVELOPE.title,
        envelope: HOURLY_TREND_ENVELOPE,
      },
    },
    {
      ...common,
      sequence: 15,
      timestamp: ts(9),
      agent_id: "trend-analyst",
      parent_agent_id: "coordinator",
      event_type: "artifact_created",
      tool_call_id: "tc_trend_3",
      artifact_id: WEEKDAY_WEEKEND_ENVELOPE.artifact_id,
      payload: {
        title: WEEKDAY_WEEKEND_ENVELOPE.title,
        envelope: WEEKDAY_WEEKEND_ENVELOPE,
      },
    },
    {
      ...common,
      sequence: 16,
      timestamp: ts(10),
      agent_id: "trend-analyst",
      parent_agent_id: "coordinator",
      event_type: "agent_completed",
      payload: {
        summary: "Evening (18-23) is peak volume; weekend fares are +5% in Queens.",
      },
      usage: { input_tokens: 1180, output_tokens: 295, latency_ms: 2110, cost_usd: 0.0042 },
    },
    {
      ...common,
      sequence: 17,
      timestamp: ts(11),
      agent_id: "coordinator",
      parent_agent_id: null,
      event_type: "run_completed",
      payload: {
        summary:
          "Synthesized spatial map and temporal trends under identical governed_valid_records basis.",
      },
      usage: { input_tokens: 640, output_tokens: 180, latency_ms: 810, cost_usd: 0.0021 },
    },
  ];
}

export function buildCancelledStream(): AgentStreamEvent[] {
  const full = buildConcurrentSpecialistStream().slice(0, 7);
  return [
    ...full,
    {
      schema_version: AGENT_EVENT_SCHEMA_VERSION,
      sequence: 8,
      timestamp: ts(4),
      trace_id: "trace_workbench_001",
      run_id: "run_workbench_001",
      tenant_id: "tenant_demo",
      agent_id: "coordinator",
      parent_agent_id: null,
      event_type: "run_cancelled",
      payload: { message: "Run cancelled by user mid-flight" },
    },
  ];
}

export function buildSingleAgentBaseline(): AgentStreamEvent[] {
  const common = {
    schema_version: AGENT_EVENT_SCHEMA_VERSION,
    trace_id: "trace_baseline_001",
    run_id: "run_baseline_001",
    tenant_id: "tenant_demo",
    agent_id: "single-agent",
    parent_agent_id: null,
  };
  return [
    {
      ...common,
      sequence: 1,
      timestamp: ts(0),
      event_type: "run_started",
      payload: {
        role: "Single ReAct Agent (Baseline)",
        message: "Running sequential queries in a single loop",
      },
    },
    {
      ...common,
      sequence: 2,
      timestamp: ts(2),
      event_type: "tool_started",
      tool_call_id: "tc_base_1",
      payload: { tool_name: "map_taxi_zones", message: "Step 1/2: Queens pickup zones" },
    },
    {
      ...common,
      sequence: 3,
      timestamp: ts(5),
      event_type: "tool_completed",
      tool_call_id: "tc_base_1",
      payload: { tool_name: "map_taxi_zones", message: "Queens pickup zones retrieved" },
    },
    {
      ...common,
      sequence: 4,
      timestamp: ts(6),
      event_type: "tool_started",
      tool_call_id: "tc_base_2",
      payload: {
        tool_name: "aggregate_taxi_data",
        message: "Step 2/2: Hourly & week-part fares",
      },
    },
    {
      ...common,
      sequence: 5,
      timestamp: ts(10),
      event_type: "tool_completed",
      tool_call_id: "tc_base_2",
      payload: { tool_name: "aggregate_taxi_data", message: "Aggregations retrieved" },
    },
    {
      ...common,
      sequence: 6,
      timestamp: ts(11),
      event_type: "artifact_created",
      tool_call_id: "tc_base_1",
      artifact_id: QUEENS_MAP_ENVELOPE.artifact_id,
      payload: {
        title: QUEENS_MAP_ENVELOPE.title,
        envelope: {
          ...QUEENS_MAP_ENVELOPE,
          provenance: { ...QUEENS_MAP_ENVELOPE.provenance, agent_id: "single-agent" },
        },
      },
    },
    {
      ...common,
      sequence: 7,
      timestamp: ts(12),
      event_type: "run_completed",
      payload: { summary: "Completed sequentially in a single agent loop." },
      usage: { input_tokens: 1850, output_tokens: 410, latency_ms: 3600, cost_usd: 0.0068 },
    },
  ];
}
