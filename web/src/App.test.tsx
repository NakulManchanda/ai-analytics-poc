import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { ContextInspector } from "./ContextInspector";
import { TimelineInspector } from "./TimelineInspector";
import { RunEvent, WorkingContextData } from "./types";

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((input: string) => {
      if (input === "/api/status") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            app: { status: "ok", service: "ai-app" },
            mcp: { status: "ok", tools: 0, resources: 0 },
          }),
        });
      }
      return Promise.resolve({
        ok: true,
        text: async () => "",
        json: async () => ({}),
      });
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  window.localStorage.clear();
});

describe("App", () => {
  it("shows the backend and discovered MCP as ready", async () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: "Taxi analytics control room" })).toBeVisible();
    expect(await screen.findByText("Backend ready")).toBeVisible();
    expect(screen.getByText("MCP discovered · 0 tools · 0 resources")).toBeVisible();
    expect(screen.getByRole("textbox", { name: "Ask about NYC taxi activity" })).toBeEnabled();
  });

  it("retries a startup status failure before showing service health", async () => {
    vi.useFakeTimers();
    const fetchStatus = vi
      .fn()
      .mockRejectedValueOnce(new Error("upstream temporarily unavailable"))
      .mockResolvedValue({
        ok: true,
        json: async () => ({
          app: { status: "ok", service: "ai-app" },
          mcp: { status: "ok", tools: 1, resources: 1 },
        }),
      });
    vi.stubGlobal("fetch", fetchStatus);

    const view = render(<App />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(500);
    });

    expect(fetchStatus).toHaveBeenCalledTimes(2);
    expect(view.getByText("Backend ready")).toBeVisible();
    expect(view.getByText("MCP discovered · 1 tools · 1 resources")).toBeVisible();
  });

  it("submits one prompt and renders the bounded final answer and aggregate usage", async () => {
    const fetchRequest = vi.fn().mockImplementation((input: string) => {
      if (input === "/api/status") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            app: { status: "ok", service: "ai-app" },
            mcp: { status: "ok", tools: 1, resources: 1 },
          }),
        });
      }
      if (input === "/api/ask") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            answer: "Alpha has the most pickups with 3 trips.",
            tool_call_id: "tool_profile",
            llm_calls: [],
            usage: { input_tokens: 16, output_tokens: 9, total_tokens: 25 },
            latency_ms: 32,
            run_id: "run_test_123",
          }),
        });
      }
      return Promise.resolve({
        ok: true,
        text: async () => "",
        json: async () => ({}),
      });
    });
    vi.stubGlobal("fetch", fetchRequest);

    render(<App />);
    const prompt = screen.getByRole("textbox", { name: "Ask about NYC taxi activity" });
    await act(async () => {
      await Promise.resolve();
    });
    fireEvent.change(prompt, { target: { value: "Which pickup zones have the most trips?" } });
    fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));

    expect(await screen.findByText("Alpha has the most pickups with 3 trips.")).toBeVisible();
    expect(screen.getByText("25 tokens · 32 ms")).toBeVisible();
    expect(fetchRequest).toHaveBeenCalledWith("/api/ask", expect.objectContaining({ method: "POST" }));
  });

  it("renders a controlled prompt error without exposing provider details", async () => {
    const fetchRequest = vi.fn().mockImplementation((input: string) => {
      if (input === "/api/status") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            app: { status: "ok", service: "ai-app" },
            mcp: { status: "ok", tools: 1, resources: 1 },
          }),
        });
      }
      return Promise.resolve({
        ok: false,
        json: async () => ({ detail: { code: "mcp_tool_error", retryable: true } }),
      });
    });
    vi.stubGlobal("fetch", fetchRequest);

    render(<App />);
    const prompt = screen.getByRole("textbox", { name: "Ask about NYC taxi activity" });
    fireEvent.change(prompt, { target: { value: "What dataset is available?" } });
    fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));

    expect(await screen.findByText("The query service is temporarily unavailable. Try again.")).toBeVisible();
  });

  it("renders the Timeline Inspector and Context Reducer Inspector tabs", async () => {
    render(<App />);

    expect(screen.getByRole("tab", { name: "Run Timeline (SSE)" })).toBeVisible();
    expect(screen.getByRole("tab", { name: "Working Context Panel" })).toBeVisible();
    expect(screen.getByText("Run Timeline Inspector")).toBeVisible();
  });

  it("switches to the Context tab and displays the context inspector", async () => {
    render(<App />);

    const contextTab = screen.getByRole("tab", { name: "Working Context Panel" });
    fireEvent.click(contextTab);

    expect(screen.getByText("Context Reducer Inspector")).toBeVisible();
  });

  it("uses backend-issued IDs from the first turn and reuses the conversation on the second", async () => {
    let turnCount = 0;
    const fetchRequest = vi.fn().mockImplementation((input: string) => {
      if (input === "/api/status") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            app: { status: "ok", service: "ai-app" },
            mcp: { status: "ok", tools: 1, resources: 1 },
          }),
        });
      }
      if (input === "/api/ask") {
        turnCount += 1;
        return Promise.resolve({
          ok: true,
          json: async () => ({
            answer: `Answer for turn ${turnCount}.`,
            tool_call_id: `tool_${turnCount}`,
            query_id: `query_${turnCount}`,
            usage: { input_tokens: 20, output_tokens: 10, total_tokens: 30 },
            latency_ms: 25,
            run_id: `run_turn_${turnCount}`,
            conversation_id: "conv_backend_123",
          }),
        });
      }
      if (input === "/api/conversations/conv_backend_123") {
        const messages = turnCount === 1
          ? [
              { message_id: "msg_1", sequence: 1, role: "user", content: "First query: top pickup zones", created_at: "2026-08-25T00:00:00Z" },
              { message_id: "msg_2", sequence: 2, role: "assistant", content: "Answer for turn 1.", created_at: "2026-08-25T00:00:01Z" },
            ]
          : [
              { message_id: "msg_1", sequence: 1, role: "user", content: "First query: top pickup zones", created_at: "2026-08-25T00:00:00Z" },
              { message_id: "msg_2", sequence: 2, role: "assistant", content: "Answer for turn 1.", created_at: "2026-08-25T00:00:01Z" },
              { message_id: "msg_3", sequence: 3, role: "user", content: "Second query: fare distribution", created_at: "2026-08-25T00:00:02Z" },
              { message_id: "msg_4", sequence: 4, role: "assistant", content: "Answer for turn 2.", created_at: "2026-08-25T00:00:03Z" },
            ];
        return Promise.resolve({
          ok: true,
          json: async () => ({
            conversation_id: "conv_backend_123",
            messages,
            runs: Array.from({ length: turnCount }, (_, index) => ({
              run_id: `run_turn_${index + 1}`, message_id: `msg_${index * 2 + 1}`,
              status: "completed", started_at: "2026-08-25T00:00:00Z", completed_at: "2026-08-25T00:00:01Z",
              input_tokens: 20, output_tokens: 10, estimated_cost_usd: 0.001, steps: [],
            })),
          }),
        });
      }
      return Promise.resolve({
        ok: true,
        text: async () => "",
        json: async () => ({}),
      });
    });
    vi.stubGlobal("fetch", fetchRequest);

    render(<App />);
    const prompt = screen.getByRole("textbox", { name: "Ask about NYC taxi activity" });

    // Turn 1
    fireEvent.change(prompt, { target: { value: "First query: top pickup zones" } });
    fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));
    expect(await screen.findByText("Answer for turn 1.", { selector: ".answer" })).toBeVisible();
    expect(screen.getByLabelText("Conversation and current run identity")).toHaveTextContent("Conversation: conv_backend_123");
    expect(screen.getByLabelText("Conversation and current run identity")).toHaveTextContent("Current Run: run_turn_1");
    expect(fetchRequest).toHaveBeenCalledWith(
      "/api/ask",
      expect.objectContaining({ body: JSON.stringify({ prompt: "First query: top pickup zones" }) }),
    );

    // Turn 2
    fireEvent.change(prompt, { target: { value: "Second query: fare distribution" } });
    fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));
    expect(await screen.findByText("Answer for turn 2.", { selector: ".answer" })).toBeVisible();
    expect(screen.getByLabelText("Conversation and current run identity")).toHaveTextContent("Conversation: conv_backend_123");
    expect(screen.getByLabelText("Conversation and current run identity")).toHaveTextContent("Current Run: run_turn_2");
    expect(fetchRequest).toHaveBeenCalledWith(
      "/api/ask",
      expect.objectContaining({
        body: JSON.stringify({
          prompt: "Second query: fare distribution",
          conversation_id: "conv_backend_123",
        }),
      }),
    );
    expect(await screen.findByText("First query: top pickup zones")).toBeVisible();
    expect(screen.getAllByText("Answer for turn 1.")).toHaveLength(1);

    // Switch to context tab and verify working context panel
    const contextTab = screen.getByRole("tab", { name: "Working Context Panel" });
    fireEvent.click(contextTab);

    expect(screen.getByText("Waiting for context reduction event from SSE stream…")).toBeVisible();
  });

  it("reloads durable messages and run metadata for the backend conversation", async () => {
    window.localStorage.setItem("ai-analytics-conversation-id", "conv_durable_456");
    const fetchRequest = vi.fn().mockImplementation((input: string) => {
      if (input === "/api/status") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            app: { status: "ok", service: "ai-app" },
            mcp: { status: "ok", tools: 1, resources: 1 },
          }),
        });
      }
      if (input === "/api/conversations/conv_durable_456") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            conversation_id: "conv_durable_456",
            messages: [
              { message_id: "msg_1", sequence: 1, role: "user", content: "First durable question", created_at: "2026-08-25T00:00:00Z" },
              { message_id: "msg_2", sequence: 2, role: "assistant", content: "First durable answer", created_at: "2026-08-25T00:00:01Z" },
            ],
            runs: [
              { run_id: "run_durable_1", message_id: "msg_1", status: "completed", started_at: "2026-08-25T00:00:00Z", completed_at: "2026-08-25T00:00:01Z", input_tokens: 11, output_tokens: 7, estimated_cost_usd: 0.0003, steps: [] },
            ],
          }),
        });
      }
      if (input === "/api/runs/run_durable_1/events") {
        return Promise.resolve({
          ok: true,
          text: async () => `data: ${JSON.stringify({
            event_id: "evt_durable_terminal", event_type: "run.completed", run_id: "run_durable_1",
            conversation_id: "conv_durable_456", sequence: 1, timestamp: "2026-08-25T00:00:02Z",
            payload: {
              input_tokens: 11, output_tokens: 7, total_tokens: 18, estimated_cost_usd: 0.0003,
              end_to_end_latency_ms: 41, proposal_llm_latency_ms: 12, tool_latency_ms: 8,
              final_answer_llm_latency_ms: 15, ttft: { available: false, reason: "non_streaming_blocking" },
            },
          })}\n\n`,
        });
      }
      return Promise.resolve({ ok: true, json: async () => ({}) });
    });
    vi.stubGlobal("fetch", fetchRequest);

    render(<App />);

    expect(await screen.findByText("First durable question")).toBeVisible();
    expect(screen.getByText("First durable answer")).toBeVisible();
    expect(screen.getByLabelText("Conversation and current run identity")).toHaveTextContent("Conversation: conv_durable_456");
    expect(screen.getByText("run_durable_1")).toBeVisible();
    expect(fetchRequest).toHaveBeenCalledWith("/api/conversations/conv_durable_456");
    expect(await screen.findByText("End-to-end latency")).toBeVisible();
    expect(screen.getByText("41 ms")).toBeVisible();
    expect(screen.getByText("$0.0003")).toBeVisible();
    expect(screen.getByText("Unavailable (non-streaming)")).toBeVisible();
  });

  it("does not let a stale mount hydration overwrite a submitted run", async () => {
    window.localStorage.setItem("ai-analytics-conversation-id", "conv_old");
    let resolveOldSnapshot: ((value: unknown) => void) | undefined;
    const oldSnapshot = new Promise((resolve) => { resolveOldSnapshot = resolve; });
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: string) => {
      if (input === "/api/status") return Promise.resolve({ ok: true, json: async () => ({ app: { status: "ok", service: "ai-app" }, mcp: { status: "ok" } }) });
      if (input === "/api/conversations/conv_old") return oldSnapshot.then((snapshot) => ({ ok: true, json: async () => snapshot }));
      if (input === "/api/ask") return Promise.resolve({ ok: true, json: async () => ({ answer: "Fresh answer", usage: { input_tokens: 1, output_tokens: 1, total_tokens: 2 }, latency_ms: 2, conversation_id: "conv_fresh", run_id: "run_fresh", llm_calls: [] }) });
      if (input === "/api/conversations/conv_fresh") return Promise.resolve({ ok: true, json: async () => ({ conversation_id: "conv_fresh", messages: [], runs: [] }) });
      return Promise.resolve({ ok: true, text: async () => "" });
    }));

    render(<App />);
    fireEvent.change(screen.getByRole("textbox", { name: "Ask about NYC taxi activity" }), { target: { value: "Fresh question" } });
    fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));
    expect(await screen.findByText("Fresh answer")).toBeVisible();

    await act(async () => {
      resolveOldSnapshot?.({
        conversation_id: "conv_old",
        messages: [{ message_id: "old_msg", sequence: 1, role: "user", content: "Old question", created_at: "2026-08-25T00:00:00Z" }],
        runs: [{ run_id: "run_old", message_id: "old_msg", status: "completed", started_at: "2026-08-25T00:00:00Z", completed_at: null, input_tokens: 1, output_tokens: 1, estimated_cost_usd: 0.001, steps: [] }],
      });
    });

    expect(screen.getByLabelText("Conversation and current run identity")).toHaveTextContent("conv_fresh");
    expect(screen.getByLabelText("Conversation and current run identity")).toHaveTextContent("run_fresh");
    expect(screen.queryByText("Old question")).not.toBeInTheDocument();
  });

  it("clears prior run context and telemetry before inspecting another run", async () => {
    window.localStorage.setItem("ai-analytics-conversation-id", "conv_switch");
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: string) => {
      if (input === "/api/status") return Promise.resolve({ ok: true, json: async () => ({ app: { status: "ok", service: "ai-app" }, mcp: { status: "ok" } }) });
      if (input === "/api/conversations/conv_switch") return Promise.resolve({ ok: true, json: async () => ({
        conversation_id: "conv_switch", messages: [],
        runs: [{ run_id: "run_first", message_id: null, status: "completed", started_at: "2026-08-25T00:00:00Z", completed_at: null, input_tokens: 1, output_tokens: 1, estimated_cost_usd: 0.001, steps: [] }],
      }) });
      if (input === "/api/runs/run_first/events") return Promise.resolve({ ok: true, text: async () => `data: ${JSON.stringify({ event_id: "evt_first", event_type: "run.completed", run_id: "run_first", conversation_id: "conv_switch", sequence: 1, timestamp: "2026-08-25T00:00:01Z", payload: { input_tokens: 1, output_tokens: 1, total_tokens: 2, estimated_cost_usd: 0.001, end_to_end_latency_ms: 9, ttft: { available: false } } })}\n\n` });
      return Promise.resolve({ ok: true, text: async () => "" });
    }));

    render(<App />);
    expect(await screen.findByText("End-to-end latency")).toBeVisible();
    fireEvent.change(screen.getByRole("textbox", { name: "Run ID" }), { target: { value: "run_second" } });
    fireEvent.click(screen.getByRole("button", { name: "Connect SSE" }));
    fireEvent.click(screen.getByRole("tab", { name: "Working Context Panel" }));

    expect(screen.getByText("Waiting for context reduction event from SSE stream…")).toBeVisible();
    expect(screen.queryByText("End-to-end latency")).not.toBeInTheDocument();
  });

  it("renders terminal SSE telemetry without inventing TTFT", async () => {
    const terminalEvent = {
      event_id: "evt_terminal",
      event_type: "run.completed",
      run_id: "run_telemetry_1",
      conversation_id: "conv_telemetry_1",
      sequence: 4,
      timestamp: "2026-08-25T00:00:04Z",
      payload: {
        input_tokens: 21,
        output_tokens: 8,
        total_tokens: 29,
        estimated_cost_usd: 0.0015,
        end_to_end_latency_ms: 52,
        proposal_llm_latency_ms: 12,
        tool_latency_ms: 10,
        final_answer_llm_latency_ms: 20,
        ttft: { available: false, reason: "non_streaming_blocking" },
      },
    };
    vi.stubGlobal("fetch", vi.fn().mockImplementation((input: string) => {
      if (input === "/api/status") {
        return Promise.resolve({ ok: true, json: async () => ({ app: { status: "ok", service: "ai-app" }, mcp: { status: "ok" } }) });
      }
      if (input === "/api/ask") {
        return Promise.resolve({
          ok: true,
          json: async () => ({
            answer: "Telemetry answer.", usage: { input_tokens: 21, output_tokens: 8, total_tokens: 29 }, latency_ms: 52,
            conversation_id: "conv_telemetry_1", run_id: "run_telemetry_1", llm_calls: [],
          }),
        });
      }
      return Promise.resolve({ ok: true, text: async () => `data: ${JSON.stringify(terminalEvent)}\n\n` });
    }));

    render(<App />);
    fireEvent.change(screen.getByRole("textbox", { name: "Ask about NYC taxi activity" }), { target: { value: "Show telemetry" } });
    fireEvent.click(screen.getByRole("button", { name: "Run analysis" }));

    expect(await screen.findByText("End-to-end latency")).toBeVisible();
    expect(screen.getByText("52 ms")).toBeVisible();
    expect(screen.getByText("12 ms")).toBeVisible();
    expect(screen.getByText("10 ms")).toBeVisible();
    expect(screen.getByText("20 ms")).toBeVisible();
    expect(screen.getByText("21")).toBeVisible();
    expect(screen.getByText("8")).toBeVisible();
    expect(screen.getByText("$0.0015")).toBeVisible();
    expect(screen.getByText("Unavailable (non-streaming)")).toBeVisible();
  });
});

describe("ContextInspector Component", () => {
  const sampleContext: WorkingContextData = {
    conversation_summary: "user: Initial question | assistant: Initial answer",
    current_user_message: "What is the average fare for JFK trips?",
    recent_messages: [
      { message_id: "m1", role: "user", content: "Tell me top zones", sequence: 1 },
      { message_id: "m2", role: "assistant", content: "JFK is #1", sequence: 2 },
    ],
    available_tools: ["query_taxi_data"],
    dataset_schema: {
      dataset: "nyc-yellow-taxi",
      columns: ["PULocationID", "fare_amount", "trip_distance"],
    },
    recent_tool_observations: [
      {
        query_id: "qry_jfk_fares_101",
        columns: ["pickup_zone", "avg_fare"],
        row_count: 2,
        preview_rows: [
          ["JFK Airport", 54.5],
          ["LaGuardia", 42.0],
        ],
        artifact_ref: "artifact://nyc-taxi/queries/qry_jfk_fares_101",
        execution_duration_ms: 18,
      },
    ],
    assumptions: [],
    artifacts: ["artifact://nyc-taxi/queries/qry_jfk_fares_101"],
    failures: [],
    remaining_budget: {
      current_iteration: 2,
      max_iterations: 6,
      remaining_iterations: 4,
      remaining_tool_calls: 6,
      remaining_llm_calls: 4,
      remaining_input_tokens: 28500,
      remaining_estimated_cost_usd: 0.0925,
      max_tool_calls: 8,
      max_llm_calls: 6,
      max_input_tokens: 30000,
      max_estimated_cost_usd: 0.1,
    },
    stored_message_count: 6,
    included_message_count: 3,
    schema_size_bytes: 142,
  };

  it("renders stored vs included divergence badges and thesis statement", () => {
    render(<ContextInspector context={sampleContext} runId="run_demo_1" />);

    expect(screen.getByText("Diverged: Stored != Context")).toBeVisible();
    expect(screen.getByText("6")).toBeVisible(); // stored
    expect(screen.getByText("3")).toBeVisible(); // included
    expect(screen.getByText("142 B")).toBeVisible(); // schema size
    expect(screen.getByText(/durable conversation != current LLM context/)).toBeVisible();
    expect(screen.getByText(/user: Initial question \| assistant: Initial answer/)).toBeVisible();
  });

  it("renders observations table, artifact reference, and budget meters", () => {
    render(<ContextInspector context={sampleContext} runId="run_demo_1" />);

    expect(screen.getAllByText("qry_jfk_fares_101")[0]).toBeVisible();
    expect(screen.getByText("JFK Airport")).toBeVisible();
    expect(screen.getByText("54.5")).toBeVisible();
    expect(screen.getAllByText("artifact://nyc-taxi/queries/qry_jfk_fares_101")[0]).toBeVisible();

    // Budget counters
    expect(screen.getByText("2 / 6")).toBeVisible();
    expect(screen.getByText("6 / 8")).toBeVisible();
    expect(screen.getByText("28,500 / 30,000")).toBeVisible();
    expect(screen.getByText("$0.0925 / $0.10")).toBeVisible();
  });
});

describe("TimelineInspector Component", () => {
  it("connects to SSE stream and displays streamed events in sequence", async () => {
    const mockEvents: RunEvent[] = [
      {
        event_id: "evt_1",
        event_type: "run.received",
        run_id: "run_test_sse",
        conversation_id: "conv_sse",
        sequence: 1,
        timestamp: "2026-08-19T00:00:00Z",
        payload: { prompt_summary: "Top pickups", status: "in_progress" },
      },
      {
        event_id: "evt_2",
        event_type: "llm.started",
        run_id: "run_test_sse",
        conversation_id: "conv_sse",
        sequence: 2,
        timestamp: "2026-08-19T00:00:01Z",
        payload: { phase: "proposal" },
      },
      {
        event_id: "evt_3",
        event_type: "tool.completed",
        run_id: "run_test_sse",
        conversation_id: "conv_sse",
        sequence: 3,
        tool_call_id: "tcall_1",
        query_id: "qry_1",
        timestamp: "2026-08-19T00:00:02Z",
        payload: { query_id: "qry_1", row_count: 5, duration_ms: 14 },
      },
      {
        event_id: "evt_4",
        event_type: "run.completed",
        run_id: "run_test_sse",
        conversation_id: "conv_sse",
        sequence: 4,
        timestamp: "2026-08-19T00:00:03Z",
        payload: { status: "completed", total_tokens: 35, estimated_cost_usd: 0.0012, latency_ms: 45 },
      },
    ];

    const sseResponseText = mockEvents.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");

    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation((url: string) => {
        if (url.includes("/api/runs/run_test_sse/events")) {
          return Promise.resolve({
            ok: true,
            text: async () => sseResponseText,
          });
        }
        return Promise.resolve({ ok: true, json: async () => ({}) });
      }),
    );

    render(<TimelineInspector runId="run_test_sse" />);

    expect(await screen.findByText("run.received")).toBeVisible();
    expect(screen.getByText("llm.started")).toBeVisible();
    expect(screen.getByText("tool.completed")).toBeVisible();
    expect(screen.getByText("run.completed")).toBeVisible();

    // Click event to expand details
    fireEvent.click(screen.getByText("tool.completed"));
    expect(screen.getByText("Event ID:")).toBeVisible();
    expect(screen.getByText("evt_3")).toBeVisible();
  });
});
