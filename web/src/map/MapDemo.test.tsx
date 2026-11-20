import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import App from "../App";

describe("?demo=map", () => {
  afterEach(() => {
    cleanup();
    window.history.pushState({}, "", "/");
  });

  it("shows the fixture map under a fixture heading", async () => {
    window.history.pushState({}, "", "/?demo=map");
    render(<App />);
    expect(screen.getByRole("heading", { name: "Fixture demo, not live data" })).toBeInTheDocument();
    expect(await screen.findByRole("img")).toBeInTheDocument();
  });
});
