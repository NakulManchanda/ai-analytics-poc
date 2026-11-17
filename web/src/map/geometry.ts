// Zone geometry loading and projection. Projection runs once per geometry load.

export interface Feature {
  properties: { location_id: number; zone: string; borough: string };
  geometry: { type: "Polygon" | "MultiPolygon"; coordinates: number[][][] | number[][][][] };
}
export interface Geometry {
  features: Feature[];
}
export interface Shape {
  id: number;
  zone: string;
  borough: string;
  d: string;
  box: [number, number, number, number];
}
export interface Projected {
  shapes: Shape[];
  byId: ReadonlyMap<number, Shape>;
  height: number;
  allView: string;
  /** View box per borough, computed once from the zone bounds. */
  boroughViews: Readonly<Record<string, string>>;
}

export const VIEW_WIDTH = 1000;
export const BOROUGH_VIEWS = ["Manhattan", "Brooklyn", "Queens", "Bronx", "Staten Island", "EWR"] as const;

let geometryPromise: Promise<Geometry> | undefined;
export function loadGeometry(): Promise<Geometry> {
  geometryPromise ??= import("./taxi-zones.v1.json").then((m) => m.default as unknown as Geometry);
  return geometryPromise;
}

export function boundsView(shapes: Shape[]): string {
  const x0 = Math.min(...shapes.map((s) => s.box[0])), y0 = Math.min(...shapes.map((s) => s.box[1]));
  const x1 = Math.max(...shapes.map((s) => s.box[2])), y1 = Math.max(...shapes.map((s) => s.box[3]));
  const pad = Math.max(x1 - x0, y1 - y0) * 0.05 + 1;
  return `${(x0 - pad).toFixed(1)} ${(y0 - pad).toFixed(1)} ${(x1 - x0 + 2 * pad).toFixed(1)} ${(y1 - y0 + 2 * pad).toFixed(1)}`;
}

// Equirectangular projection with a cos(lat0) x-scale into a fixed-width coordinate space.
export function projectShapes(geometry: Geometry): Projected {
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
    return { id: f.properties.location_id, zone: f.properties.zone, borough: f.properties.borough, d, box };
  });
  const height = (maxLat - minLat) * scale;
  const boroughViews: Record<string, string> = {};
  for (const b of BOROUGH_VIEWS) {
    const inBorough = shapes.filter((s) => s.borough === b);
    if (inBorough.length) boroughViews[b] = boundsView(inBorough);
  }
  return {
    shapes,
    byId: new Map(shapes.map((s) => [s.id, s])),
    height,
    allView: `0 0 ${VIEW_WIDTH} ${height.toFixed(1)}`,
    boroughViews,
  };
}
