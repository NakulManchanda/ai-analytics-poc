import { cleanup, render, screen } from "@testing-library/react";
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
    expect(screen.getByRole("textbox", { name: "Ask about NYC taxi activity" })).toBeDisabled();
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
});
