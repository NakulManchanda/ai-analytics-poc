import { useEffect, useMemo, useState } from "react";

import { fetchZoneMap, getCachedZoneMap, MapRequestError, type FilterValue, type ZoneMapRequest, type ZoneRole } from "./api";
import { MEASURES, type MapLaunch } from "./fromTool";
import { ZoneMap } from "./ZoneMap";

const DEBOUNCE_MS = 150;
const PANEL_FILTERS = ["pickup_week_part", "pickup_hour_min", "pickup_hour_max"];

const MEASURE_LABELS: Record<string, string> = {
  trip_count: "Trip count",
  trip_share: "Trip share",
  average_fare: "Average fare",
  median_fare: "Median fare",
  average_tip: "Average tip",
  median_tip: "Median tip",
  total_tips: "Total tips",
  tip_rate: "Tip rate",
  average_total_amount: "Average total amount",
  average_distance: "Average distance",
  median_distance: "Median distance",
  average_duration_minutes: "Average duration (min)",
  median_duration_minutes: "Median duration (min)",
  p95_duration_minutes: "P95 duration (min)",
  average_passenger_count: "Average passenger count",
};
const CURATED = ["trip_count", "average_fare", "average_tip", "tip_rate", "average_distance", "average_duration_minutes"];
const HOURS = Array.from({ length: 24 }, (_, i) => i);

export function zonePrompt(zoneName: string): string {
  return `Tell me more about taxi activity in the ${zoneName} zone.`;
}

type Load =
  | { status: "loading"; artifact: unknown }
  | { status: "ready"; artifact: unknown }
  | { status: "error"; artifact: unknown; message: string };

interface Props {
  launch?: MapLaunch;
  onAskAboutZone: (prompt: string) => void;
  onClose: () => void;
}

function hourOf(v: FilterValue | undefined): string {
  return typeof v === "number" && Number.isInteger(v) && v >= 0 && v <= 23 ? String(v) : "any";
}

export function MapPanel({ launch, onAskAboutZone, onClose }: Props) {
  const initial = launch?.request;
  const initialFilters = initial?.filters ?? {};
  // Filters the controls do not offer are carried along unchanged unless the user explicitly drops them.
  const [extraFilters, setExtraFilters] = useState(() =>
    Object.fromEntries(Object.entries(initialFilters).filter(([k]) => !PANEL_FILTERS.includes(k))),
  );
  const [role, setRole] = useState<ZoneRole>(initial?.zone_role ?? "pickup");
  const [measure, setMeasure] = useState(initial?.measure ?? "trip_count");
  const [weekPart, setWeekPart] = useState(
    initialFilters.pickup_week_part === "weekday" || initialFilters.pickup_week_part === "weekend"
      ? initialFilters.pickup_week_part
      : "all",
  );
  const [hourMin, setHourMin] = useState(hourOf(initialFilters.pickup_hour_min));
  const [hourMax, setHourMax] = useState(hourOf(initialFilters.pickup_hour_max));
  const [load, setLoad] = useState<Load>({ status: "loading", artifact: null });
  const [dropped, setDropped] = useState(launch?.dropped ?? false);
  const [attempt, setAttempt] = useState(0);

  const measures = (MEASURES as readonly string[]).includes(measure) && !CURATED.includes(measure) ? [...CURATED, measure] : CURATED;
  const hoursInvalid = hourMin !== "any" && hourMax !== "any" && Number(hourMin) > Number(hourMax);

  const request = useMemo<ZoneMapRequest>(() => {
    const filters: Record<string, FilterValue> = { ...extraFilters };
    if (weekPart !== "all") filters.pickup_week_part = weekPart;
    if (hourMin !== "any") filters.pickup_hour_min = Number(hourMin);
    if (hourMax !== "any") filters.pickup_hour_max = Number(hourMax);
    return { zone_role: role, measure, ...(Object.keys(filters).length ? { filters } : {}) };
  }, [role, measure, weekPart, hourMin, hourMax, extraFilters]);

  useEffect(() => {
    if (hoursInvalid) return;
    const cached = getCachedZoneMap(request);
    if (cached !== undefined) {
      setLoad({ status: "ready", artifact: cached });
      return;
    }
    setLoad((cur) => ({ status: "loading", artifact: cur.artifact }));
    const ctl = new AbortController();
    const timer = window.setTimeout(async () => {
      try {
        const artifact = await fetchZoneMap(request, ctl.signal);
        if (!ctl.signal.aborted) setLoad({ status: "ready", artifact });
      } catch (error) {
        if (ctl.signal.aborted) return;
        setLoad((cur) => ({
          status: "error",
          artifact: cur.artifact,
          message: error instanceof MapRequestError ? error.message : "Map request failed",
        }));
      }
    }, DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
      ctl.abort();
    };
  }, [request, hoursInvalid, attempt]);

  const extraList = Object.entries(extraFilters);

  return (
    <section className="map-panel" aria-label="Zone map">
      <div className="map-panel-header">
        <h2>Zone map</h2>
        <button type="button" className="btn-text" onClick={onClose}>
          Close map
        </button>
      </div>
      <div className="map-controls">
        <div role="group" aria-label="Zone role" className="zm-views">
          {(["pickup", "dropoff"] as const).map((r) => (
            <button key={r} type="button" className="zm-view-btn" aria-pressed={role === r} onClick={() => setRole(r)}>
              {r === "pickup" ? "Pickup zones" : "Dropoff zones"}
            </button>
          ))}
        </div>
        <label>
          Measure
          <select value={measure} onChange={(e) => setMeasure(e.target.value)}>
            {measures.map((m) => (
              <option key={m} value={m}>{MEASURE_LABELS[m] ?? m}</option>
            ))}
          </select>
        </label>
        <label>
          Week part
          <select value={weekPart} onChange={(e) => setWeekPart(e.target.value)}>
            <option value="all">All days</option>
            <option value="weekday">Weekday</option>
            <option value="weekend">Weekend</option>
          </select>
        </label>
        <label>
          From hour
          <select value={hourMin} onChange={(e) => setHourMin(e.target.value)}>
            <option value="any">Any</option>
            {HOURS.map((h) => <option key={h} value={h}>{h}</option>)}
          </select>
        </label>
        <label>
          To hour
          <select value={hourMax} onChange={(e) => setHourMax(e.target.value)}>
            <option value="any">Any</option>
            {HOURS.map((h) => <option key={h} value={h}>{h}</option>)}
          </select>
        </label>
      </div>
      {extraList.length > 0 && (
        <p className="map-note">{`Also applying from the chat run: ${extraList.map(([k, v]) => `${k} = ${String(v)}`).join(", ")}`}</p>
      )}
      {dropped && <p className="map-note" role="status">Some filters not applied.</p>}
      {hoursInvalid && <p className="map-note" role="alert">The start hour is after the end hour.</p>}
      {load.status === "loading" && !hoursInvalid && <p className="zm-loading" role="status">Loading map…</p>}
      {load.status === "error" && !hoursInvalid && (
        <p className="zm-notice" role="alert">
          {load.message}{" "}
          <button type="button" className="btn-text" onClick={() => setAttempt((n) => n + 1)}>
            Retry
          </button>
          {extraList.length > 0 && (
            <>
              {" "}
              <button
                type="button"
                className="btn-text"
                onClick={() => {
                  setExtraFilters({});
                  setDropped(true);
                }}
              >
                Drop chat filters and retry
              </button>
            </>
          )}
        </p>
      )}
      {load.artifact !== null && load.status !== "error" && (
        <div className={load.status === "loading" ? "map-stale" : undefined}>
          <ZoneMap artifact={load.artifact} interactive onAskAboutZone={(zone) => onAskAboutZone(zonePrompt(zone))} />
        </div>
      )}
    </section>
  );
}
