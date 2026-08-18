import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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

  it("shows the backend and discovered MCP as ready", async () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: "Taxi analytics control room" })).toBeVisible();
    expect(await screen.findByText("Backend ready")).toBeVisible();
    expect(screen.getByText("MCP discovered · 0 tools · 0 resources")).toBeVisible();
    expect(screen.getByRole("textbox", { name: "Ask about NYC taxi activity" })).toBeDisabled();
  });
});
