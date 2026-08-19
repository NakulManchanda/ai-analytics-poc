import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

describe("App", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          app: { status: "ok", service: "ai-app" },
          mcp: { status: "ok", tools: 0, resources: 0 },
        }),
      }),
    );
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

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
      return Promise.resolve({
        ok: true,
        json: async () => ({
          answer: "Alpha has the most pickups with 3 trips.",
          tool_call_id: "tool_profile",
          llm_calls: [],
          usage: { input_tokens: 16, output_tokens: 9, total_tokens: 25 },
          latency_ms: 32,
        }),
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
});
