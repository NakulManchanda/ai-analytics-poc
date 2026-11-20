// Client for POST /api/map/zones with an in-memory response cache. Callers pass an
// AbortSignal so a superseded request can be cancelled; the artifact is validated by ZoneMap.

export type ZoneRole = "pickup" | "dropoff";
export type FilterValue = string | number | boolean;
export interface ZoneMapRequest {
  zone_role: ZoneRole;
  measure: string;
  filters?: Record<string, FilterValue>;
}

export class MapRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "MapRequestError";
  }
}

const GENERIC_ERROR = "Map request failed";
const CACHE_LIMIT = 32;
const cache = new Map<string, unknown>();

export function canonicalKey(request: ZoneMapRequest): string {
  const filters = Object.fromEntries(
    Object.entries(request.filters ?? {})
      .filter(([, v]) => v !== undefined)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)),
  );
  return JSON.stringify({ zone_role: request.zone_role, measure: request.measure, filters });
}

export function getCachedZoneMap(request: ZoneMapRequest): unknown | undefined {
  return cache.get(canonicalKey(request));
}

export function clearZoneMapCache(): void {
  cache.clear();
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    const detail = body.detail as { message?: unknown } | undefined;
    if (detail && typeof detail === "object" && !Array.isArray(detail) && typeof detail.message === "string") {
      return detail.message;
    }
  } catch {
    /* non-JSON body */
  }
  return GENERIC_ERROR;
}

export async function fetchZoneMap(request: ZoneMapRequest, signal?: AbortSignal): Promise<unknown> {
  const key = canonicalKey(request);
  if (cache.has(key)) {
    const hit = cache.get(key);
    cache.delete(key);
    cache.set(key, hit); // refresh recency
    return hit;
  }
  let response: Response;
  try {
    response = await fetch("/api/map/zones", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: key,
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new MapRequestError(GENERIC_ERROR, 0);
  }
  if (!response.ok) throw new MapRequestError(await errorMessage(response), response.status);
  const body: unknown = await response.json();
  cache.set(key, body);
  if (cache.size > CACHE_LIMIT) cache.delete(cache.keys().next().value as string);
  return body;
}
