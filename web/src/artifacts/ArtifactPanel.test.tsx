import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import {
  ARTIFACT_ENVELOPE_SCHEMA_VERSION,
  parseArtifactEnvelope,
  synthesizeArtifacts,
  type ArtifactEnvelope,
} from "./envelope";
import { ArtifactPanel, SynthesisBanner } from "./ArtifactPanel";

afterEach(cleanup);

const baseProv = {
  run_id: "run_1",
  agent_id: "trend-analyst",
  tool_call_id: "tc_1",
  tool_name: "aggregate_taxi_data",
  query_id: "q_1",
  dataset: "nyc-yellow-taxi-2024-01",
  time_window: "2024-01-01..2024-01-31",
  filter_basis: "governed_valid_records",
};

describe("ArtifactEnvelope validation and ArtifactPanel rendering (#235)", () => {
  it("renders table, time_series, and segment_comparison after JSON rehydration", () => {
    const envelopes: ArtifactEnvelope[] = [
      {
        schema_version: ARTIFACT_ENVELOPE_SCHEMA_VERSION,
        artifact_id: "tbl_1",
        kind: "table",
        title: "Top pickup boroughs",
        provenance: baseProv,
        body: {
          columns: [
            { key: "borough", label: "Borough" },
            { key: "trips", label: "Trips", unit: "trips" },
          ],
          rows: [
            ["Manhattan", 2616329],
            ["Queens", 279000],
          ],
          row_count: 2,
          truncated: false,
        },
      },
      {
        schema_version: ARTIFACT_ENVELOPE_SCHEMA_VERSION,
        artifact_id: "ts_1",
        kind: "time_series",
        title: "Hourly pickups",
        provenance: baseProv,
        body: {
          x_label: "pickup_hour",
          y_label: "trip_count",
          unit: "trips",
          points: [
            { x: "08:00", y: 120000 },
            { x: "18:00", y: 215000 },
          ],
          total_y: 335000,
        },
      },
      {
        schema_version: ARTIFACT_ENVELOPE_SCHEMA_VERSION,
        artifact_id: "seg_1",
        kind: "segment_comparison",
        title: "Weekday vs Weekend fare",
        provenance: baseProv,
        body: {
          dimension: "borough",
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
          ],
        },
      },
    ];

    for (const env of envelopes) {
      const rehydrated = JSON.parse(JSON.stringify(env));
      const parsed = parseArtifactEnvelope(rehydrated);
      expect(parsed.ok).toBe(true);
      const { unmount } = render(<ArtifactPanel rawEnvelope={rehydrated} />);
      expect(screen.getByText(env.title)).toBeInTheDocument();
      unmount();
    }
  });

  it("rejects mismatched chart sums, mismatched deltas, and unsafe markup", () => {
    const badSum = {
      schema_version: ARTIFACT_ENVELOPE_SCHEMA_VERSION,
      artifact_id: "ts_bad",
      kind: "time_series",
      title: "Bad sum",
      provenance: baseProv,
      body: {
        x_label: "hour",
        y_label: "trips",
        points: [{ x: "08:00", y: 100 }],
        total_y: 999,
      },
    };
    expect(parseArtifactEnvelope(badSum)).toMatchObject({ ok: false, code: "sum_mismatch" });
    render(<ArtifactPanel rawEnvelope={badSum} />);
    expect(screen.getByRole("alert")).toHaveTextContent("sum_mismatch");

    const badDelta = {
      schema_version: ARTIFACT_ENVELOPE_SCHEMA_VERSION,
      artifact_id: "seg_bad",
      kind: "segment_comparison",
      title: "Bad delta",
      provenance: baseProv,
      body: {
        dimension: "borough",
        metric: "fare",
        baseline_label: "Weekday",
        comparison_label: "Weekend",
        rows: [{ segment: "Queens", baseline: 40, comparison: 50, delta: 999 }],
      },
    };
    expect(parseArtifactEnvelope(badDelta)).toMatchObject({ ok: false, code: "delta_mismatch" });

    const xss = {
      schema_version: ARTIFACT_ENVELOPE_SCHEMA_VERSION,
      artifact_id: "tbl_xss",
      kind: "table",
      title: "Safe title",
      provenance: baseProv,
      body: {
        columns: [{ key: "k", label: "Label" }],
        rows: [["<script>alert(1)</script>"]],
        row_count: 1,
      },
    };
    expect(parseArtifactEnvelope(xss)).toMatchObject({ ok: false, code: "unsafe_content" });
  });

  it("flags cross-agent basis mismatch, time mismatch, truncation, and value contradictions", () => {
    const a: ArtifactEnvelope = {
      schema_version: ARTIFACT_ENVELOPE_SCHEMA_VERSION,
      artifact_id: "a1",
      kind: "table",
      title: "Spatial Manhattan trips",
      provenance: { ...baseProv, agent_id: "spatial-analyst", truncated: true },
      body: {
        columns: [
          { key: "borough", label: "Borough" },
          { key: "trips", label: "Trips" },
        ],
        rows: [["Manhattan", 2616329]],
        row_count: 5,
        truncated: true,
      },
    };
    const b: ArtifactEnvelope = {
      schema_version: ARTIFACT_ENVELOPE_SCHEMA_VERSION,
      artifact_id: "a2",
      kind: "table",
      title: "Trend Manhattan trips",
      provenance: {
        ...baseProv,
        agent_id: "trend-analyst",
        filter_basis: "raw_unfiltered",
        time_window: "2024-01-01..2024-01-15",
      },
      body: {
        columns: [
          { key: "borough", label: "Borough" },
          { key: "trips", label: "Trips" },
        ],
        rows: [["Manhattan", 2000000]],
        row_count: 1,
      },
    };

    const report = synthesizeArtifacts([a, b]);
    expect(report.consistent).toBe(false);
    const codes = new Set(report.issues.map((i) => i.code));
    expect(codes).toEqual(
      new Set([
        "basis_mismatch",
        "time_window_mismatch",
        "truncated_source",
        "value_contradiction",
      ]),
    );

    render(<SynthesisBanner report={report} />);
    expect(screen.getByRole("alert")).toHaveTextContent("value_contradiction");
    expect(screen.getByRole("alert")).toHaveTextContent("basis_mismatch");
  });
});
