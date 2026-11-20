import { describe, expect, it } from "vitest";

import { parseMapArtifact } from "./artifact";
import valid from "./fixtures/pickup-count-choropleth.v1.json";

const zoneIds = new Set(Array.from({ length: 263 }, (_, i) => i + 1));
const invalid = import.meta.glob("./fixtures/invalid/*.json", { eager: true, import: "default" });
const clone = () => structuredClone(valid) as any;

describe("parseMapArtifact", () => {
  it("accepts the golden valid fixture", () => {
    expect(parseMapArtifact(valid, zoneIds).ok).toBe(true);
  });

  it("rejects every invalid fixture", () => {
    expect(Object.keys(invalid)).toHaveLength(5);
    for (const [name, payload] of Object.entries(invalid)) {
      expect(parseMapArtifact(payload, zoneIds).ok, name).toBe(false);
    }
  });

  it.each([
    ["unknown key", (p: any) => (p.layers[0].extra = 1)],
    ["wrong schema", (p: any) => (p.schema = "x")],
    ["wrong version", (p: any) => (p.version = 2)],
    ["wrong geography", (p: any) => (p.geography.version = "v2")],
    ["unknown zone", (p: any) => (p.layers[0].regions[0].location_ids = [999])],
    ["empty ids", (p: any) => (p.layers[0].regions[0].location_ids = [])],
    ["zone in two regions", (p: any) => (p.layers[0].regions[1].location_ids = p.layers[0].regions[0].location_ids)],
    ["hotspot layer", (p: any) => p.layers.push({ kind: "hotspot", location_ids: [1] })],
    ["two highlights", (p: any) => p.layers.push(structuredClone(p.layers[1]))],
    ["no choropleth", (p: any) => p.layers.shift()],
    ["bad palette", (p: any) => (p.layers[0].scale.palette = "red")],
    ["bad interpretation", (p: any) => (p.provenance.interpretation = "causal")],
    ["bad reason", (p: any) => (p.unmapped[0].reason = "x")],
    ["view coordinates", (p: any) => (p.view = { fit: "all", zoom: 9 })],
    ["view unknown zone", (p: any) => (p.view = { fit: { location_ids: [999] } })],
    ["min above max", (p: any) => Object.assign(p.layers[0].scale, { min: 5, max: 1 })],
    ["string value", (p: any) => (p.layers[0].regions[0].value = "5")],
    ["long title", (p: any) => (p.title = "x".repeat(121))],
    ["control char", (p: any) => (p.title = "a\u0000b")],
    ["too many filters", (p: any) => (p.filters = Object.fromEntries(Array.from({ length: 17 }, (_, i) => [`k${i}`, i])))],
    ["unknown tool name", (p: any) => (p.evidence.tool_name = "run_sql")],
    ["non-scalar filter", (p: any) => (p.filters = { k: ["a"] })],
  ])("rejects %s", (_name, mutate) => {
    const p = clone();
    mutate(p);
    expect(parseMapArtifact(p, zoneIds).ok).toBe(false);
  });

  it("accepts null run and tool call ids from the map endpoint", () => {
    const p = clone();
    p.evidence.run_id = null;
    p.evidence.tool_call_id = null;
    p.evidence.tool_name = "map_taxi_zones";
    expect(parseMapArtifact(p, zoneIds).ok).toBe(true);
  });

  it("accepts fit on named zones", () => {
    const p = clone();
    p.view = { fit: { location_ids: [132, 138] } };
    expect(parseMapArtifact(p, zoneIds).ok).toBe(true);
  });

  it("does not echo payload content in errors", () => {
    const p = clone();
    p.title = "SECRET\u0000";
    p.extra = "SECRET";
    const result = parseMapArtifact(p, zoneIds);
    expect(result.ok).toBe(false);
    expect(JSON.stringify(result)).not.toContain("SECRET");
  });

  it("rejects non-objects", () => {
    expect(parseMapArtifact(null, zoneIds).ok).toBe(false);
    expect(parseMapArtifact("x", zoneIds).ok).toBe(false);
  });
});
