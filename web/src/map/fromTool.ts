import type { FilterValue, ZoneMapRequest } from "./api";

export const MEASURES = [
  "trip_count", "trip_share", "average_fare", "median_fare", "average_tip", "median_tip", "total_tips",
  "tip_rate", "average_total_amount", "average_distance", "median_distance", "average_duration_minutes",
  "median_duration_minutes", "p95_duration_minutes", "average_passenger_count",
] as const;

/** Panel launch state derived from a chat tool call. */
export interface MapLaunch {
  request: ZoneMapRequest;
  /** True when part of the tool call (a filter or measure) could not be carried over. */
  dropped: boolean;
}

/** Maps a `tool.requested` payload for aggregate_taxi_data grouped by a single zone dimension to a map launch. */
export function launchFromToolRequest(payload: Record<string, unknown>): MapLaunch | null {
  if (payload.tool_name !== "aggregate_taxi_data") return null;
  const dims = Array.isArray(payload.dimensions) ? payload.dimensions : [];
  if (dims.length !== 1) return null;
  const dim = dims[0];
  if (dim !== "pickup_zone" && dim !== "dropoff_zone") return null;
  const pickup = dim === "pickup_zone";
  let dropped = false;
  const first = Array.isArray(payload.measures) ? payload.measures[0] : undefined;
  const known = typeof first === "string" && (MEASURES as readonly string[]).includes(first);
  if (!known) dropped = true;
  const filters: Record<string, FilterValue> = {};
  const raw = payload.filters;
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    for (const [k, v] of Object.entries(raw)) {
      if (v === null || v === undefined) continue;
      if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") filters[k] = v;
      else dropped = true;
    }
  }
  return {
    request: {
      zone_role: pickup ? "pickup" : "dropoff",
      measure: known ? (first as string) : "trip_count",
      ...(Object.keys(filters).length ? { filters } : {}),
    },
    dropped,
  };
}
