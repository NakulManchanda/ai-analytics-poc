import { cleanup, render, screen } from "@testing-library/react";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TimelineInspector } from "./TimelineInspector";
import { RunEvent, RunTelemetry, WorkingContextData } from "./types";

class MockEventSource {
  static instances: MockEventSource[] = [];

  readonly close = vi.fn();
  private readonly listeners = new Map<string, Array<(event: MessageEvent) => void>>();

  constructor(readonly url: string) {
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: MessageEvent) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  emit(type: string, event: RunEvent) {
    for (const listener of this.listeners.get(type) ?? []) {
      listener(new MessageEvent(type, { data: JSON.stringify(event) }));
    }
  }
}

const runReceived: RunEvent = {
  event_id: "evt_received",
  event_type: "run.received",
  run_id: "run_same",
  conversation_id: "conversation_1",
  sequence: 1,
  timestamp: "2026-08-25T00:00:00Z",
  payload: { prompt_summary: "Top pickup zones" },
};

const reducedContext: WorkingContextData = {
  stored_message_count: 2,
  included_message_count: 2,
  schema_size_bytes: 100,
};

const contextReduced: RunEvent = {
  event_id: "evt_context",
  event_type: "context.reduced",
  run_id: "run_same",
  conversation_id: "conversation_1",
  sequence: 2,
  timestamp: "2026-08-25T00:00:01Z",
  payload: { working_context: reducedContext },
};

const runCompleted: RunEvent = {
  event_id: "evt_completed",
  event_type: "run.completed",
  run_id: "run_same",
  conversation_id: "conversation_1",
  sequence: 3,
  timestamp: "2026-08-25T00:00:02Z",
  payload: { total_tokens: 20, estimated_cost_usd: 0.001, latency_ms: 10 },
};

describe("TimelineInspector SSE lifecycle", () => {
  beforeEach(() => {
    MockEventSource.instances = [];
    vi.stubGlobal("EventSource", MockEventSource);
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("keeps the stream and timeline when a parent supplies a new context callback", async () => {
    const initialContextUpdate = vi.fn();
    const latestContextUpdate = vi.fn();
    const view = render(
      <TimelineInspector runId="run_same" onWorkingContextUpdate={initialContextUpdate} />,
    );

    const stream = MockEventSource.instances[0];
    await act(async () => {
      stream.emit("run.received", runReceived);
    });
    expect(screen.getByText("run.received")).toBeVisible();

    view.rerender(<TimelineInspector runId="run_same" onWorkingContextUpdate={latestContextUpdate} />);

    expect(MockEventSource.instances).toHaveLength(1);
    expect(stream.close).not.toHaveBeenCalled();
    expect(screen.getByText("run.received")).toBeVisible();

    await act(async () => {
      stream.emit("context.reduced", contextReduced);
    });
    expect(latestContextUpdate).toHaveBeenCalledWith("run_same", reducedContext);
    expect(initialContextUpdate).not.toHaveBeenCalled();

    await act(async () => {
      stream.emit("run.completed", runCompleted);
    });
    view.rerender(<TimelineInspector runId="run_same" onWorkingContextUpdate={vi.fn()} />);

    expect(MockEventSource.instances).toHaveLength(1);
    expect(stream.close).toHaveBeenCalledTimes(1);
    expect(screen.getByText("run.completed")).toBeVisible();
    expect(screen.getByText("COMPLETED")).toBeVisible();
  });

  it("closes one stream and creates one replacement when run identity changes", () => {
    const view = render(<TimelineInspector runId="run_first" />);
    const firstStream = MockEventSource.instances[0];

    view.rerender(<TimelineInspector runId="run_second" />);

    expect(firstStream.close).toHaveBeenCalledTimes(1);
    expect(MockEventSource.instances).toHaveLength(2);
    expect(MockEventSource.instances[1].url).toBe("/api/runs/run_second/events");
  });

  it("forwards only terminal SSE telemetry to the run telemetry consumer", async () => {
    const onRunTelemetryUpdate = vi.fn();
    render(<TimelineInspector runId="run_same" onRunTelemetryUpdate={onRunTelemetryUpdate} />);

    await act(async () => {
      MockEventSource.instances[0].emit("run.completed", {
        ...runCompleted,
        payload: {
          input_tokens: 13,
          output_tokens: 7,
          total_tokens: 20,
          estimated_cost_usd: 0.0012,
          end_to_end_latency_ms: 47,
          proposal_llm_latency_ms: 11,
          tool_latency_ms: 9,
          final_answer_llm_latency_ms: 19,
          ttft: { available: false, reason: "non_streaming_blocking" },
        },
      });
    });

    expect(onRunTelemetryUpdate).toHaveBeenCalledWith("run_same", {
      input_tokens: 13,
      output_tokens: 7,
      total_tokens: 20,
      estimated_cost_usd: 0.0012,
      end_to_end_latency_ms: 47,
      proposal_llm_latency_ms: 11,
      tool_latency_ms: 9,
      final_answer_llm_latency_ms: 19,
      ttft: { available: false, reason: "non_streaming_blocking" },
    } satisfies RunTelemetry);
  });

  it("renders tool.failed and detailed run.failed error events", async () => {
    render(<TimelineInspector runId="run_same" />);

    const stream = MockEventSource.instances[0];
    await act(async () => {
      stream.emit("tool.failed", {
        event_id: "evt_tool_fail",
        event_type: "tool.failed",
        run_id: "run_same",
        conversation_id: "conversation_1",
        sequence: 2,
        timestamp: "2026-08-25T00:00:01Z",
        payload: {
          tool_name: "average_trip_metrics",
          error: "Unknown tool: average_trip_metrics",
        },
      });
      stream.emit("run.failed", {
        event_id: "evt_run_fail",
        event_type: "run.failed",
        run_id: "run_same",
        conversation_id: "conversation_1",
        sequence: 3,
        timestamp: "2026-08-25T00:00:02Z",
        payload: {
          failure_code: "mcp_tool_error",
          error: "Unknown tool: average_trip_metrics",
        },
      });
    });

    expect(screen.getByText("tool.failed")).toBeVisible();
    expect(screen.getByText(/Tool average_trip_metrics failed: Unknown tool: average_trip_metrics/)).toBeVisible();
    expect(screen.getByText("run.failed")).toBeVisible();
    expect(screen.getByText(/Run failed: \[mcp_tool_error\] Unknown tool: average_trip_metrics/)).toBeVisible();
  });

  it("renders run.cancel_requested and run.cancelled events with warning badges", async () => {
    const telemetryUpdate = vi.fn();
    render(<TimelineInspector runId="run_same" onRunTelemetryUpdate={telemetryUpdate} />);

    const stream = MockEventSource.instances[0];
    await act(async () => {
      stream.emit("run.cancel_requested", {
        event_id: "evt_cancel_req",
        event_type: "run.cancel_requested",
        run_id: "run_same",
        conversation_id: "conversation_1",
        sequence: 2,
        timestamp: "2026-08-25T00:00:01Z",
        payload: { status: "cancel_requested" },
      });
      stream.emit("run.cancelled", {
        event_id: "evt_cancelled",
        event_type: "run.cancelled",
        run_id: "run_same",
        conversation_id: "conversation_1",
        sequence: 3,
        timestamp: "2026-08-25T00:00:02Z",
        payload: {
          status: "cancelled",
          input_tokens: 15,
          output_tokens: 5,
          total_tokens: 20,
          estimated_cost_usd: 0.001,
          latency_ms: 50,
          failure_code: "cancelled",
        },
      });
    });

    expect(screen.getByText("run.cancel_requested")).toBeVisible();
    expect(screen.getByText("Cancellation requested by user")).toBeVisible();
    expect(screen.getByText("run.cancelled")).toBeVisible();
    expect(screen.getByText(/Run cancelled · 20 tokens consumed · 50ms/)).toBeVisible();
    expect(telemetryUpdate).toHaveBeenCalledWith(
      "run_same",
      expect.objectContaining({
        input_tokens: 15,
        output_tokens: 5,
        total_tokens: 20,
      }),
    );
  });
});
