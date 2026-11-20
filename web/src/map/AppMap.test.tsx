import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "../App";
import { clearZoneMapCache } from "./api";
import valid from "./fixtures/pickup-count-choropleth.v1.json";

// Minimal EventSource that replays registered SSE frames (same shape as the App tests).
const registry = new Map<string, string>();
class FakeEventSource {
  private listeners = new Map<string, ((e: Event) => void)[]>();
  constructor(private url: string) {
    setTimeout(() => {
      for (const frame of (registry.get(this.url) ?? "").split("\n\n").filter((f) => f.trim())) {
        const raw = frame.split("\n").find((l) => l.startsWith("data: "))!.slice(6);
        const type = JSON.parse(raw).event_type as string;
        this.listeners.get(type)?.forEach((l) => l(new MessageEvent(type, { data: raw })));
      }
    }, 0);
  }
  addEventListener(t: string, l: (e: Event) => void) {
    this.listeners.set(t, [...(this.listeners.get(t) ?? []), l]);
  }
  close() {
    this.listeners.clear();
  }
}

const ev = (id: number, type: string, payload: object, run = "run_map_1") =>
  ({ event_id: `e${id}`, event_type: type, run_id: run, conversation_id: "conv_map", sequence: id, timestamp: "2026-11-16T00:00:00Z", payload });

function stubBackend(toolPayload: object | null, toolOutcome: "tool.completed" | "tool.failed" = "tool.completed") {
  const events = [
    ...(toolPayload ? [ev(1, "tool.requested", toolPayload), ev(2, toolOutcome, {})] : []),
    ev(3, "answer.delta", { delta: "Answer text." }),
    ev(4, "run.completed", { input_tokens: 1, output_tokens: 1, total_tokens: 2, end_to_end_latency_ms: 5 }),
  ];
  registry.set("/api/runs/run_map_1/events", events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join(""));
  const fetchMock = vi.fn().mockImplementation(async (input: string) => {
    if (input === "/api/status") return { ok: true, json: async () => ({ app: { status: "ok", service: "ai-app" }, mcp: { status: "ok" } }) };
    if (input === "/api/runs") return { ok: true, json: async () => ({ run_id: "run_map_1", conversation_id: "conv_map", message_id: "m1", events_url: "/api/runs/run_map_1/events" }) };
    if (input === "/api/conversations/conv_map") {
      return {
        ok: true,
        json: async () => ({
          conversation_id: "conv_map",
          messages: [
            { message_id: "m1", sequence: 1, role: "user", content: "Top zones?", created_at: "2026-11-16T00:00:00Z" },
            { message_id: "m2", sequence: 2, role: "assistant", content: "Answer text.", created_at: "2026-11-16T00:00:01Z" },
          ],
          runs: [{ run_id: "run_map_1", message_id: "m1", status: "completed", started_at: "x", completed_at: "y", input_tokens: 1, output_tokens: 1, estimated_cost_usd: 0, steps: [] }],
        }),
      };
    }
    if (input === "/api/map/zones") return { ok: true, json: async () => valid };
    return { ok: true, text: async () => "", json: async () => ({}) };
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function runPrompt() {
  fireEvent.change(screen.getByRole("textbox", { name: "Ask about NYC taxi activity" }), { target: { value: "Top zones?" } });
  fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));
  await screen.findByText("Inspect Run");
}

describe("App map integration", () => {
  beforeEach(() => {
    clearZoneMapCache();
    vi.stubGlobal("EventSource", FakeEventSource as unknown as typeof EventSource);
  });
  afterEach(() => {
    cleanup();
    registry.clear();
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it("opens the map from the Map button and Ask about this zone fills the prompt without sending", async () => {
    const fetchMock = stubBackend(null);
    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "Map" }));
    await screen.findByRole("img", { name: /Trip count by pickup zone/ });
    fireEvent.click(document.querySelector("path[data-location-id='132']")!);
    fireEvent.click(screen.getByRole("button", { name: "Ask about this zone" }));
    expect(screen.getByRole("textbox", { name: "Ask about NYC taxi activity" })).toHaveValue(
      "Tell me more about taxi activity in the JFK Airport zone.",
    );
    expect(fetchMock.mock.calls.some((c) => c[0] === "/api/runs")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "Close map" }));
    expect(screen.queryByLabelText("Zone map")).toBeNull();
  });

  it("shows Show on map for a zone-grouped aggregate and opens a prefilled panel", async () => {
    const fetchMock = stubBackend({
      tool_name: "aggregate_taxi_data",
      dimensions: ["dropoff_zone"],
      measures: ["average_fare"],
      filters: { pickup_week_part: "weekend" },
    });
    render(<App />);
    await runPrompt();
    fireEvent.click(await screen.findByRole("button", { name: "Show on map" }));
    await screen.findByRole("img", { name: /Trip count by pickup zone/ });
    expect(screen.getByLabelText("Measure")).toHaveValue("average_fare");
    expect(screen.getByRole("button", { name: "Dropoff zones" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("Week part")).toHaveValue("weekend");
    await waitFor(() => {
      const call = fetchMock.mock.calls.find((c) => c[0] === "/api/map/zones")!;
      expect(JSON.parse((call[1] as RequestInit).body as string)).toEqual({
        zone_role: "dropoff",
        measure: "average_fare",
        filters: { pickup_week_part: "weekend" },
      });
    });
  });

  it.each([
    ["no tool call", null],
    ["a non-zone aggregation", { tool_name: "aggregate_taxi_data", dimensions: ["pickup_borough"], measures: ["trip_count"] }],
    ["a multi-dimension aggregation", { tool_name: "aggregate_taxi_data", dimensions: ["pickup_zone", "pickup_hour"], measures: ["trip_count"] }],
    ["another tool", { tool_name: "compare_taxi_segments", dimensions: ["pickup_zone"] }],
  ])("does not show Show on map for %s", async (_name, payload) => {
    stubBackend(payload);
    render(<App />);
    await runPrompt();
    expect(screen.queryByRole("button", { name: "Show on map" })).toBeNull();
  });

  it("does not show Show on map when the zone aggregation fails", async () => {
    stubBackend(
      { tool_name: "aggregate_taxi_data", dimensions: ["pickup_zone"], measures: ["trip_count"] },
      "tool.failed",
    );
    render(<App />);
    await runPrompt();
    expect(screen.queryByRole("button", { name: "Show on map" })).toBeNull();
  });

  it("tracks pending launches by tool_call_id across interleaved tool events", async () => {
    // Interleaved: zone aggregation fails while metadata tool completes, then reverse.
    const events1 = [
      ev(1, "tool.requested", { tool_name: "aggregate_taxi_data", dimensions: ["pickup_zone"], measures: ["trip_count"] }),
      ev(2, "tool.started", { tool_call_id: "call_zone", tool_name: "aggregate_taxi_data" }),
      ev(3, "tool.requested", { tool_name: "query_taxi_data", analysis: "top_pickup_zones" }),
      ev(4, "tool.started", { tool_call_id: "call_meta", tool_name: "query_taxi_data" }),
      ev(5, "tool.completed", { tool_call_id: "call_meta", query_id: "q_meta" }),
      ev(6, "tool.failed", { tool_call_id: "call_zone", tool_name: "aggregate_taxi_data", error: "boom" }),
      ev(7, "answer.delta", { delta: "Answer text." }),
      ev(8, "run.completed", { input_tokens: 1, output_tokens: 1, total_tokens: 2, end_to_end_latency_ms: 5 }),
    ];
    stubBackend(null);
    registry.set("/api/runs/run_map_1/events", events1.map((e) => `data: ${JSON.stringify(e)}\n\n`).join(""));
    render(<App />);
    await runPrompt();
    expect(screen.queryByRole("button", { name: "Show on map" })).toBeNull();
  });

  it("shows Show on map when a zone aggregation succeeds after another tool fails", async () => {
    const events2 = [
      ev(1, "tool.requested", { tool_name: "aggregate_taxi_data", dimensions: ["pickup_zone"], measures: ["trip_count"] }),
      ev(2, "tool.started", { tool_call_id: "call_zone", tool_name: "aggregate_taxi_data" }),
      ev(3, "tool.requested", { tool_name: "query_taxi_data", analysis: "top_pickup_zones" }),
      ev(4, "tool.started", { tool_call_id: "call_meta", tool_name: "query_taxi_data" }),
      ev(5, "tool.failed", { tool_call_id: "call_meta", tool_name: "query_taxi_data", error: "boom" }),
      ev(6, "tool.completed", { tool_call_id: "call_zone", query_id: "q_zone" }),
      ev(7, "answer.delta", { delta: "Answer text." }),
      ev(8, "run.completed", { input_tokens: 1, output_tokens: 1, total_tokens: 2, end_to_end_latency_ms: 5 }),
    ];
    stubBackend(null);
    registry.set("/api/runs/run_map_1/events", events2.map((e) => `data: ${JSON.stringify(e)}\n\n`).join(""));
    render(<App />);
    await runPrompt();
    expect(await screen.findByRole("button", { name: "Show on map" })).toBeInTheDocument();
  });
});
