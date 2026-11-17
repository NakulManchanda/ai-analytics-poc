import { useEffect, useId, useMemo, useState } from "react";

import { parseMapArtifact, type Choropleth, type Highlight, type MapArtifact } from "./artifact";

interface Feature {
  properties: { location_id: number; zone: string; borough: string };
  geometry: { type: "Polygon" | "MultiPolygon"; coordinates: number[][][] | number[][][][] };
}
interface Geometry {
  features: Feature[];
}
interface Shape {
  id: number;
  zone: string;
  d: string;
  box: [number, number, number, number];
}

const VIEW_WIDTH = 1000;
const STEPS = 5;
const DISCLAIMER = "Shows where values are high or low; it does not show why.";

let geometryPromise: Promise<Geometry> | undefined;
function loadGeometry(): Promise<Geometry> {
  geometryPromise ??= import("./taxi-zones.v1.json").then((m) => m.default as unknown as Geometry);
  return geometryPromise;
}

// Equirectangular projection with a cos(lat0) x-scale into a fixed-width coordinate space.
function projectShapes(geometry: Geometry): { shapes: Shape[]; height: number } {
  let minLon = Infinity, maxLon = -Infinity, minLat = Infinity, maxLat = -Infinity;
  const eachPoint = (c: unknown, fn: (lon: number, lat: number) => void): void => {
    const a = c as unknown[];
    if (typeof a[0] === "number") fn(a[0] as number, a[1] as number);
    else for (const x of a) eachPoint(x, fn);
  };
  for (const f of geometry.features) {
    eachPoint(f.geometry.coordinates, (lon, lat) => {
      minLon = Math.min(minLon, lon); maxLon = Math.max(maxLon, lon);
      minLat = Math.min(minLat, lat); maxLat = Math.max(maxLat, lat);
    });
  }
  const cos = Math.cos((((minLat + maxLat) / 2) * Math.PI) / 180);
  const scale = VIEW_WIDTH / ((maxLon - minLon) * cos);
  const px = (lon: number) => (lon - minLon) * cos * scale;
  const py = (lat: number) => (maxLat - lat) * scale;
  const shapes = geometry.features.map((f): Shape => {
    const box: Shape["box"] = [Infinity, Infinity, -Infinity, -Infinity];
    const polygons = (f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.coordinates) as number[][][][];
    const d = polygons
      .flatMap((poly) => poly)
      .map((ring) =>
        ring
          .map(([lon, lat], i) => {
            const x = px(lon), y = py(lat);
            box[0] = Math.min(box[0], x); box[1] = Math.min(box[1], y);
            box[2] = Math.max(box[2], x); box[3] = Math.max(box[3], y);
            return `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`;
          })
          .join("") + "Z",
      )
      .join("");
    return { id: f.properties.location_id, zone: f.properties.zone, d, box };
  });
  return { shapes, height: (maxLat - minLat) * scale };
}

function step(value: number, min: number, max: number): number {
  if (max <= min) return Math.floor(STEPS / 2);
  return Math.min(STEPS - 1, Math.max(0, Math.floor(((value - min) / (max - min)) * STEPS)));
}

const fmt = (n: number) => n.toLocaleString("en-US", { maximumFractionDigits: 2 });

function viewBox(artifact: MapArtifact, shapes: Shape[], height: number): string {
  const fit = artifact.view.fit;
  if (fit === "all") return `0 0 ${VIEW_WIDTH} ${height.toFixed(1)}`;
  const named = shapes.filter((s) => fit.location_ids.includes(s.id));
  const x0 = Math.min(...named.map((s) => s.box[0])), y0 = Math.min(...named.map((s) => s.box[1]));
  const x1 = Math.max(...named.map((s) => s.box[2])), y1 = Math.max(...named.map((s) => s.box[3]));
  const pad = Math.max(x1 - x0, y1 - y0) * 0.05 + 1;
  return `${(x0 - pad).toFixed(1)} ${(y0 - pad).toFixed(1)} ${(x1 - x0 + 2 * pad).toFixed(1)} ${(y1 - y0 + 2 * pad).toFixed(1)}`;
}

function Notice({ children }: { children: string }) {
  return (
    <p className="zm-notice" role="alert">
      {children}
    </p>
  );
}

export function ZoneMap({ artifact }: { artifact: unknown }) {
  const [geometry, setGeometry] = useState<Geometry | null>(null);
  const [failed, setFailed] = useState(false);
  const uid = useId();

  useEffect(() => {
    let live = true;
    loadGeometry().then(
      (g) => live && setGeometry(g),
      () => live && setFailed(true),
    );
    return () => {
      live = false;
    };
  }, []);

  // Projection runs once per geometry load, never on hover or state changes.
  const projected = useMemo(() => (geometry ? projectShapes(geometry) : null), [geometry]);
  const zoneIds = useMemo(() => new Set(projected?.shapes.map((s) => s.id) ?? []), [projected]);

  if (failed) return <Notice>Map could not be shown.</Notice>;
  if (!projected) return <p className="zm-loading">Loading map…</p>;

  const parsed = parseMapArtifact(artifact, zoneIds);
  if (!parsed.ok) return <Notice>Map could not be shown.</Notice>;
  const a = parsed.artifact;
  const choropleth = a.layers.find((l): l is Choropleth => l.kind === "choropleth")!;
  const highlight = a.layers.find((l): l is Highlight => l.kind === "highlight");
  const { palette, min, max } = choropleth.scale;
  const unit = choropleth.unit;

  const regionOf = new Map<number, { label: string; value: number }>();
  for (const r of choropleth.regions) for (const id of r.location_ids) regionOf.set(id, r);
  const highlighted = new Set(highlight?.location_ids ?? []);
  // Highlighted zones are drawn last so their stroke is not covered by neighbours.
  const ordered = [...projected.shapes].sort((x, y) => Number(highlighted.has(x.id)) - Number(highlighted.has(y.id)));
  const titleId = `${uid}-title`;
  const descId = `${uid}-desc`;
  const filters = Object.entries(a.filters);

  return (
    <figure className="zone-map">
      <svg
        role="img"
        aria-labelledby={`${titleId} ${descId}`}
        viewBox={viewBox(a, projected.shapes, projected.height)}
        className="zm-svg"
      >
        <title id={titleId}>{a.title}</title>
        <desc id={descId}>
          {`Choropleth of ${choropleth.measure} in ${unit} across ${choropleth.regions.length} regions.`}
        </desc>
        {ordered.map((s) => {
          const region = regionOf.get(s.id);
          const cls = ["zm-zone"];
          if (region) cls.push(`zm-${palette}-${step(region.value, min, max)}`);
          if (highlighted.has(s.id)) cls.push("zm-highlight");
          return (
            <path key={s.id} data-location-id={s.id} className={cls.join(" ")} d={s.d} fillRule="evenodd">
              <title>{region ? `${region.label}: ${fmt(region.value)} ${unit}` : `${s.zone}: no data`}</title>
            </path>
          );
        })}
      </svg>
      <figcaption>
        <h3 className="zm-title">{a.title}</h3>
        <div className="zm-legend" aria-label="Legend">
          <span>{`${fmt(min)} ${unit}`}</span>
          <span className="zm-swatches" aria-hidden="true">
            {Array.from({ length: STEPS }, (_, i) => (
              <span key={i} className={`zm-swatch zm-${palette}-${i}`} />
            ))}
          </span>
          <span>{`${fmt(max)} ${unit}`}</span>
          {highlight && <span className="zm-legend-highlight">{`Outlined: ${highlight.label}`}</span>}
        </div>
        <p className="zm-filters">
          {filters.length
            ? `Filters: ${filters.map(([k, v]) => `${k} = ${String(v)}`).join(", ")}`
            : "Filters: none"}
        </p>
        <p className="zm-provenance">
          {`Source: ${a.provenance.source} · dataset ${a.evidence.dataset} · run ${a.evidence.run_id} · tool call ${a.evidence.tool_call_id} · query ${a.evidence.query_id}`}
        </p>
        <p className="zm-disclaimer">{DISCLAIMER}</p>
        <table className="zm-table">
          <caption>{`${choropleth.measure} by zone`}</caption>
          <thead>
            <tr>
              <th scope="col">Zone</th>
              <th scope="col">{`Value (${unit})`}</th>
              <th scope="col">On map</th>
            </tr>
          </thead>
          <tbody>
            {choropleth.regions.map((r) => (
              <tr key={r.label}>
                <th scope="row">{r.label}</th>
                <td>{fmt(r.value)}</td>
                <td>Yes</td>
              </tr>
            ))}
            {a.unmapped.map((u, i) => (
              <tr key={`u${i}`}>
                <th scope="row">{u.label}</th>
                <td>{fmt(u.value)}</td>
                <td>{`No (${u.reason === "no_geometry" ? "no geometry" : "unknown zone"})`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </figcaption>
    </figure>
  );
}
