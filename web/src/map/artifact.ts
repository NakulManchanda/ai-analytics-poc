// Runtime validator for map artifact v1. Mirrors services/app/app/artifacts/map_artifact.py;
// both are tested against the shared fixtures in ./fixtures. Errors name rules and paths only
// and never echo payload content.

export type Palette = "blues" | "oranges";
export type Scalar = string | number | boolean;

export interface Region {
  label: string;
  location_ids: number[];
  value: number;
}
export interface Choropleth {
  kind: "choropleth";
  measure: string;
  unit: string;
  scale: { kind: "sequential"; palette: Palette; min: number; max: number };
  regions: Region[];
}
export interface Highlight {
  kind: "highlight";
  label: string;
  location_ids: number[];
}
export interface Unmapped {
  label: string;
  value: number;
  reason: "no_geometry" | "unknown_zone";
}
export interface MapArtifact {
  schema: "map_artifact";
  version: 1;
  artifact_id: string;
  geography: { id: "nyc_tlc_taxi_zones"; version: "v1" };
  evidence: {
    run_id: string;
    tool_call_id: string;
    query_id: string;
    tool_name: string;
    dataset: string;
  };
  title: string;
  layers: (Choropleth | Highlight)[];
  unmapped: Unmapped[];
  filters: Record<string, Scalar>;
  view: { fit: "all" | { location_ids: number[] } };
  provenance: {
    generated_by: "app";
    source: string;
    interpretation: "spatial_pattern_not_causal";
  };
}

export type ParseResult = { ok: true; artifact: MapArtifact } | { ok: false; errors: string[] };

const MAX_BYTES = 64 * 1024;
const MAX_ZONES = 263;
const CONTROL = /[\u0000-\u001f\u007f-\u009f]/;
type Obj = Record<string, unknown>;

export function parseMapArtifact(input: unknown, zoneIds: ReadonlySet<number>): ParseResult {
  const errors: string[] = [];
  const err = (path: string, rule: string) => {
    if (errors.length < 20) errors.push(`${path}: ${rule}`);
  };

  const obj = (v: unknown, path: string, keys: string[]): Obj | null => {
    if (typeof v !== "object" || v === null || Array.isArray(v)) {
      err(path, "expected object");
      return null;
    }
    const o = v as Obj;
    for (const k of Object.keys(o)) if (!keys.includes(k)) err(path, "unknown key");
    for (const k of keys) if (!(k in o)) err(path, `missing ${k}`);
    return o;
  };
  const text = (v: unknown, path: string, max: number): string => {
    if (typeof v !== "string" || v.length < 1 || v.length > max || CONTROL.test(v)) {
      err(path, "invalid string");
      return "";
    }
    return v;
  };
  const num = (v: unknown, path: string): number => {
    if (typeof v !== "number" || !Number.isFinite(v)) {
      err(path, "expected finite number");
      return 0;
    }
    return v;
  };
  const oneOf = <T extends string>(v: unknown, path: string, allowed: readonly T[]): T => {
    if (typeof v !== "string" || !(allowed as readonly string[]).includes(v)) {
      err(path, "unsupported value");
      return allowed[0];
    }
    return v as T;
  };
  const zones = (v: unknown, path: string): number[] => {
    if (!Array.isArray(v) || v.length < 1 || v.length > MAX_ZONES) {
      err(path, "expected 1..263 zone ids");
      return [];
    }
    if (new Set(v).size !== v.length) err(path, "duplicate zone id");
    for (const id of v) if (!Number.isInteger(id) || !zoneIds.has(id as number)) err(path, "unknown zone");
    return v as number[];
  };
  const list = (v: unknown, path: string, max: number): unknown[] => {
    if (!Array.isArray(v) || v.length > max) {
      err(path, "expected array within limit");
      return [];
    }
    return v;
  };

  let size = Infinity;
  try {
    size = new TextEncoder().encode(JSON.stringify(input)).length;
  } catch {
    /* not serializable */
  }
  if (size > MAX_BYTES) return { ok: false, errors: ["artifact: too large or not serializable"] };

  const root = obj(input, "artifact", [
    "schema", "version", "artifact_id", "geography", "evidence", "title",
    "layers", "unmapped", "filters", "view", "provenance",
  ]);
  if (!root) return { ok: false, errors };

  if (root.schema !== "map_artifact") err("schema", "unsupported value");
  if (root.version !== 1) err("version", "unsupported value");
  if (typeof root.artifact_id !== "string" || !/^map_[0-9a-f]{8,64}$/.test(root.artifact_id)) {
    err("artifact_id", "invalid id");
  }
  const geo = obj(root.geography, "geography", ["id", "version"]);
  if (geo && (geo.id !== "nyc_tlc_taxi_zones" || geo.version !== "v1")) err("geography", "unsupported value");
  const ev = obj(root.evidence, "evidence", ["run_id", "tool_call_id", "query_id", "tool_name", "dataset"]);
  if (ev) {
    for (const k of ["run_id", "tool_call_id", "query_id"]) text(ev[k], `evidence.${k}`, 128);
    for (const k of ["tool_name", "dataset"]) text(ev[k], `evidence.${k}`, 80);
  }
  text(root.title, "title", 120);

  let choroplethCount = 0;
  let highlightCount = 0;
  const claimed = new Set<number>();
  list(root.layers, "layers", 64).forEach((raw, i) => {
    const path = `layers[${i}]`;
    const kind = typeof raw === "object" && raw !== null ? (raw as Obj).kind : undefined;
    if (kind === "choropleth") {
      choroplethCount++;
      const l = obj(raw, path, ["kind", "measure", "unit", "scale", "regions"]);
      if (!l) return;
      text(l.measure, `${path}.measure`, 40);
      text(l.unit, `${path}.unit`, 24);
      const s = obj(l.scale, `${path}.scale`, ["kind", "palette", "min", "max"]);
      if (s) {
        oneOf(s.kind, `${path}.scale.kind`, ["sequential"]);
        oneOf(s.palette, `${path}.scale.palette`, ["blues", "oranges"]);
        if (num(s.min, `${path}.scale.min`) > num(s.max, `${path}.scale.max`)) err(`${path}.scale`, "min above max");
      }
      list(l.regions, `${path}.regions`, MAX_ZONES).forEach((r, j) => {
        const rp = `${path}.regions[${j}]`;
        const region = obj(r, rp, ["label", "location_ids", "value"]);
        if (!region) return;
        text(region.label, `${rp}.label`, 80);
        num(region.value, `${rp}.value`);
        for (const id of zones(region.location_ids, `${rp}.location_ids`)) {
          if (claimed.has(id)) err(rp, "zone in more than one region");
          claimed.add(id);
        }
      });
    } else if (kind === "highlight") {
      highlightCount++;
      const l = obj(raw, path, ["kind", "label", "location_ids"]);
      if (!l) return;
      text(l.label, `${path}.label`, 80);
      zones(l.location_ids, `${path}.location_ids`);
    } else {
      err(path, "unsupported layer kind");
    }
  });
  if (choroplethCount !== 1 || highlightCount > 1) err("layers", "expected one choropleth and at most one highlight");

  list(root.unmapped, "unmapped", 300).forEach((u, i) => {
    const p = `unmapped[${i}]`;
    const row = obj(u, p, ["label", "value", "reason"]);
    if (!row) return;
    text(row.label, `${p}.label`, 80);
    num(row.value, `${p}.value`);
    oneOf(row.reason, `${p}.reason`, ["no_geometry", "unknown_zone"]);
  });

  if (typeof root.filters !== "object" || root.filters === null || Array.isArray(root.filters)) {
    err("filters", "expected object");
  } else {
    const entries = Object.entries(root.filters as Obj);
    if (entries.length > 16) err("filters", "too many keys");
    for (const [k, v] of entries) {
      text(k, "filters key", 40);
      const scalar = typeof v === "string" ? v.length <= 80 && !CONTROL.test(v) : typeof v === "boolean" || (typeof v === "number" && Number.isFinite(v));
      if (!scalar) err(`filters.${k}`, "expected scalar value");
    }
  }

  const view = obj(root.view, "view", ["fit"]);
  if (view && view.fit !== "all") {
    const fit = obj(view.fit, "view.fit", ["location_ids"]);
    if (fit) zones(fit.location_ids, "view.fit.location_ids");
  }

  const prov = obj(root.provenance, "provenance", ["generated_by", "source", "interpretation"]);
  if (prov) {
    oneOf(prov.generated_by, "provenance.generated_by", ["app"]);
    text(prov.source, "provenance.source", 200);
    oneOf(prov.interpretation, "provenance.interpretation", ["spatial_pattern_not_causal"]);
  }

  return errors.length ? { ok: false, errors } : { ok: true, artifact: input as MapArtifact };
}
