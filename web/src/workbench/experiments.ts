import {
  ARTIFACT_ENVELOPE_SCHEMA_VERSION,
  type ArtifactEnvelope,
} from "../artifacts/envelope";
import {
  HOURLY_TREND_ENVELOPE,
  QUEENS_MAP_ENVELOPE,
  WEEKDAY_WEEKEND_ENVELOPE,
  buildConcurrentSpecialistStream,
  buildSingleAgentBaseline,
} from "./fixtures";
import { AGENT_EVENT_SCHEMA_VERSION, type AgentStreamEvent } from "./state";

export interface ExperimentScenario {
  id:
    | "borough_pickups"
    | "year_boundary_guard"
    | "airport_anomaly"
    | "weekday_weekend_linked"
    | "invalid_date_control";
  label: string;
  question: string;
  expectedOutcome: string;
  events: AgentStreamEvent[];
  baselineEvents?: AgentStreamEvent[];
}

const BOROUGH_TABLE_ENVELOPE: ArtifactEnvelope = {
  schema_version: ARTIFACT_ENVELOPE_SCHEMA_VERSION,
  artifact_id: "art_tbl_borough_pickups",
  kind: "table",
  title: "Pickup volume by NYC borough (Jan 2024)",
  provenance: {
    run_id: "run_exp_borough",
    agent_id: "spatial-analyst",
    tool_call_id: "tc_exp_borough_1",
    tool_name: "aggregate_taxi_data",
    query_id: "q_exp_borough_v1",
    dataset: "nyc-yellow-taxi-2024-01",
    time_window: "2024-01-01..2024-01-31",
    filter_basis: "governed_valid_records",
    truncated: false,
  },
  body: {
    columns: [
      { key: "borough", label: "Borough" },
      { key: "trip_count", label: "Trip count", unit: "trips" },
      { key: "avg_fare", label: "Average fare", unit: "USD" },
    ],
    rows: [
      ["Manhattan", 2616329, 15.62],
      ["Queens", 279000, 48.72],
      ["Brooklyn", 18420, 26.4],
      ["Bronx", 5120, 29.15],
    ],
    row_count: 4,
    truncated: false,
  },
};

function makeCommon(runId: string) {
  return {
    schema_version: AGENT_EVENT_SCHEMA_VERSION,
    trace_id: `trace_${runId}`,
    run_id: runId,
    tenant_id: "tenant_demo",
  };
}

export function buildExperimentScenarios(): ExperimentScenario[] {
  const boroughCommon = makeCommon("run_exp_borough");
  const boundaryCommon = makeCommon("run_exp_boundary");
  const invalidCommon = makeCommon("run_exp_invalid");

  return [
    {
      id: "borough_pickups",
      label: "1. Busiest Pickup Zones by Borough",
      question: "Which NYC taxi zones and boroughs have the highest pickup volume in Jan 2024?",
      expectedOutcome:
        "Renders aligned borough summary table + Queens pickup zone choropleth map under identical governed_valid_records basis.",
      events: [
        {
          ...boroughCommon,
          sequence: 1,
          timestamp: "2026-04-20T15:00:00Z",
          agent_id: "coordinator",
          parent_agent_id: null,
          event_type: "run_started",
          payload: { role: "Coordinator", message: "Dispatching spatial pickup ranking" },
        },
        {
          ...boroughCommon,
          sequence: 2,
          timestamp: "2026-04-20T15:00:01Z",
          agent_id: "spatial-analyst",
          parent_agent_id: "coordinator",
          event_type: "agent_started",
          payload: { role: "Spatial Analyst", message: "Querying borough and zone pickup counts" },
        },
        {
          ...boroughCommon,
          sequence: 3,
          timestamp: "2026-04-20T15:00:03Z",
          agent_id: "spatial-analyst",
          parent_agent_id: "coordinator",
          event_type: "artifact_created",
          tool_call_id: "tc_exp_borough_1",
          artifact_id: BOROUGH_TABLE_ENVELOPE.artifact_id,
          payload: { title: BOROUGH_TABLE_ENVELOPE.title, envelope: BOROUGH_TABLE_ENVELOPE },
        },
        {
          ...boroughCommon,
          sequence: 4,
          timestamp: "2026-04-20T15:00:04Z",
          agent_id: "spatial-analyst",
          parent_agent_id: "coordinator",
          event_type: "artifact_created",
          tool_call_id: "tc_spatial_1",
          artifact_id: QUEENS_MAP_ENVELOPE.artifact_id,
          payload: { title: QUEENS_MAP_ENVELOPE.title, envelope: QUEENS_MAP_ENVELOPE },
        },
        {
          ...boroughCommon,
          sequence: 5,
          timestamp: "2026-04-20T15:00:05Z",
          agent_id: "spatial-analyst",
          parent_agent_id: "coordinator",
          event_type: "agent_completed",
          payload: { summary: "Manhattan leads total volume; JFK and LGA dominate Queens." },
          usage: { input_tokens: 740, output_tokens: 185, latency_ms: 1120, cost_usd: 0.0024 },
        },
        {
          ...boroughCommon,
          sequence: 6,
          timestamp: "2026-04-20T15:00:06Z",
          agent_id: "coordinator",
          parent_agent_id: null,
          event_type: "run_completed",
          payload: { summary: "Completed borough + zone pickup breakdown." },
        },
      ],
    },
    {
      id: "year_boundary_guard",
      label: "2. 2023 vs 2024 Boundary Check",
      question: "Compare 2023 vs 2024 monthly fare trends across NYC boroughs.",
      expectedOutcome:
        "Pinned dataset only covers nyc-yellow-taxi-2024-01; refuses to invent 2023 values and emits zero fabricated charts.",
      events: [
        {
          ...boundaryCommon,
          sequence: 1,
          timestamp: "2026-04-20T15:05:00Z",
          agent_id: "coordinator",
          parent_agent_id: null,
          event_type: "run_started",
          payload: { role: "Coordinator", message: "Checking dataset coverage for 2023 and 2024" },
        },
        {
          ...boundaryCommon,
          sequence: 2,
          timestamp: "2026-04-20T15:05:01Z",
          agent_id: "trend-analyst",
          parent_agent_id: "coordinator",
          event_type: "agent_started",
          payload: { role: "Trend Analyst", message: "Inspecting available dataset partitions" },
        },
        {
          ...boundaryCommon,
          sequence: 3,
          timestamp: "2026-04-20T15:05:02Z",
          agent_id: "trend-analyst",
          parent_agent_id: "coordinator",
          event_type: "agent_completed",
          payload: {
            summary:
              "Dataset boundary reached: only nyc-yellow-taxi-2024-01 is loaded. 2023 data is unavailable; no comparison chart was fabricated.",
          },
          usage: { input_tokens: 410, output_tokens: 92, latency_ms: 490, cost_usd: 0.0012 },
        },
        {
          ...boundaryCommon,
          sequence: 4,
          timestamp: "2026-04-20T15:05:03Z",
          agent_id: "coordinator",
          parent_agent_id: null,
          event_type: "run_completed",
          payload: {
            summary:
              "Reported pinned-dataset boundary (Jan 2024 only) without inventing 2023 figures.",
          },
        },
      ],
    },
    {
      id: "airport_anomaly",
      label: "3. Airport Anomaly Drill-Down",
      question: "Why are Queens average fares much higher than Manhattan, and which zones drive it?",
      expectedOutcome:
        "Concurrent Spatial + Trend specialists surface JFK/LGA concentration map, hourly buckets, and weekday/weekend fare comparison.",
      events: buildConcurrentSpecialistStream(),
      baselineEvents: buildSingleAgentBaseline(),
    },
    {
      id: "weekday_weekend_linked",
      label: "4. Weekday vs Weekend Linked Filter",
      question: "Compare weekday vs weekend fares and hourly pickup volume in Jan 2024.",
      expectedOutcome:
        "Linked segment_comparison + time_series artifacts pass cross-agent synthesis check.",
      events: [
        {
          ...makeCommon("run_exp_weekpart"),
          sequence: 1,
          timestamp: "2026-04-20T15:10:00Z",
          agent_id: "coordinator",
          parent_agent_id: null,
          event_type: "run_started",
          payload: { role: "Coordinator", message: "Evaluating weekday vs weekend shifts" },
        },
        {
          ...makeCommon("run_exp_weekpart"),
          sequence: 2,
          timestamp: "2026-04-20T15:10:01Z",
          agent_id: "trend-analyst",
          parent_agent_id: "coordinator",
          event_type: "agent_started",
          payload: { role: "Trend Analyst", message: "Comparing weekday vs weekend fares" },
        },
        {
          ...makeCommon("run_exp_weekpart"),
          sequence: 3,
          timestamp: "2026-04-20T15:10:03Z",
          agent_id: "trend-analyst",
          parent_agent_id: "coordinator",
          event_type: "artifact_created",
          tool_call_id: "tc_trend_3",
          artifact_id: WEEKDAY_WEEKEND_ENVELOPE.artifact_id,
          payload: { title: WEEKDAY_WEEKEND_ENVELOPE.title, envelope: WEEKDAY_WEEKEND_ENVELOPE },
        },
        {
          ...makeCommon("run_exp_weekpart"),
          sequence: 4,
          timestamp: "2026-04-20T15:10:04Z",
          agent_id: "trend-analyst",
          parent_agent_id: "coordinator",
          event_type: "artifact_created",
          tool_call_id: "tc_trend_2",
          artifact_id: HOURLY_TREND_ENVELOPE.artifact_id,
          payload: { title: HOURLY_TREND_ENVELOPE.title, envelope: HOURLY_TREND_ENVELOPE },
        },
        {
          ...makeCommon("run_exp_weekpart"),
          sequence: 5,
          timestamp: "2026-04-20T15:10:05Z",
          agent_id: "trend-analyst",
          parent_agent_id: "coordinator",
          event_type: "agent_completed",
          payload: { summary: "Weekend fares rise +9.97% in Manhattan and +5.0% in Queens." },
        },
        {
          ...makeCommon("run_exp_weekpart"),
          sequence: 6,
          timestamp: "2026-04-20T15:10:06Z",
          agent_id: "coordinator",
          parent_agent_id: null,
          event_type: "run_completed",
          payload: { summary: "Linked weekday/weekend artifacts verified." },
        },
      ],
    },
    {
      id: "invalid_date_control",
      label: "Control: Invalid Date / Empty Data",
      question: "Show pickups between 2025-02-30 and 2025-03-01.",
      expectedOutcome:
        "Fails closed with explicit tool_failed / run_failed status and zero fabricated artifacts.",
      events: [
        {
          ...invalidCommon,
          sequence: 1,
          timestamp: "2026-04-20T15:15:00Z",
          agent_id: "coordinator",
          parent_agent_id: null,
          event_type: "run_started",
          payload: { role: "Coordinator", message: "Validating requested date window" },
        },
        {
          ...invalidCommon,
          sequence: 2,
          timestamp: "2026-04-20T15:15:01Z",
          agent_id: "trend-analyst",
          parent_agent_id: "coordinator",
          event_type: "tool_failed",
          tool_call_id: "tc_invalid_1",
          payload: {
            tool_name: "aggregate_taxi_data",
            error: "Invalid calendar date '2025-02-30' and outside pinned dataset window.",
          },
        },
        {
          ...invalidCommon,
          sequence: 3,
          timestamp: "2026-04-20T15:15:02Z",
          agent_id: "trend-analyst",
          parent_agent_id: "coordinator",
          event_type: "agent_failed",
          payload: { error: "Aborted query: invalid filter date." },
        },
        {
          ...invalidCommon,
          sequence: 4,
          timestamp: "2026-04-20T15:15:03Z",
          agent_id: "coordinator",
          parent_agent_id: null,
          event_type: "run_failed",
          payload: { error: "Run failed closed due to invalid date filter." },
        },
      ],
    },
  ];
}
