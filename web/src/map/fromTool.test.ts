import { describe, expect, it } from "vitest";

import { launchFromToolRequest } from "./fromTool";

describe("launchFromToolRequest", () => {
  it("maps a pickup-zone aggregation to a request", () => {
    expect(
      launchFromToolRequest({
        tool_name: "aggregate_taxi_data",
        dimensions: ["pickup_zone"],
        measures: ["average_fare"],
        filters: { pickup_week_part: "weekend", pickup_hour_min: 6, payment_type: 1 },
        limit: 10,
      }),
    ).toEqual({
      request: {
        zone_role: "pickup",
        measure: "average_fare",
        filters: { pickup_week_part: "weekend", pickup_hour_min: 6, payment_type: 1 },
      },
      dropped: false,
    });
  });

  it("flags extra measures as dropped", () => {
    expect(
      launchFromToolRequest({
        tool_name: "aggregate_taxi_data",
        dimensions: ["pickup_zone"],
        measures: ["average_fare", "trip_count"],
      }),
    ).toEqual({
      request: { zone_role: "pickup", measure: "average_fare" },
      dropped: true,
    });
  });

  it("maps dropoff zones and flags unknown measures and non-scalar filters", () => {
    expect(
      launchFromToolRequest({
        tool_name: "aggregate_taxi_data",
        dimensions: ["dropoff_zone"],
        measures: ["nope"],
        filters: { pickup_borough: ["Queens", "Bronx"], pickup_week_part: "weekday" },
      }),
    ).toEqual({
      request: { zone_role: "dropoff", measure: "trip_count", filters: { pickup_week_part: "weekday" } },
      dropped: true,
    });
  });

  it.each([
    ["other tool", { tool_name: "compare_taxi_segments", dimensions: ["pickup_zone"] }],
    ["non-zone dimension", { tool_name: "aggregate_taxi_data", dimensions: ["pickup_borough"], measures: ["trip_count"] }],
    ["no dimensions", { tool_name: "aggregate_taxi_data", measures: ["trip_count"] }],
    ["both zone dimensions", { tool_name: "aggregate_taxi_data", dimensions: ["pickup_zone", "dropoff_zone"] }],
    ["multiple dimensions with a zone", { tool_name: "aggregate_taxi_data", dimensions: ["pickup_zone", "pickup_hour"], measures: ["trip_count"] }],
  ])("returns null for %s", (_name, payload) => {
    expect(launchFromToolRequest(payload)).toBeNull();
  });
});
