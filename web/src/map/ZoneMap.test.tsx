import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import valid from "./fixtures/pickup-count-choropleth.v1.json";
import { ZoneMap } from "./ZoneMap";

const invalid = import.meta.glob("./fixtures/invalid/*.json", { eager: true, import: "default" });

describe("ZoneMap", () => {
  afterEach(cleanup);

  it("draws all 263 zones with filled regions and a highlight on the top zone", async () => {
    const { container } = render(<ZoneMap artifact={valid} />);
    await screen.findByRole("img");
    const paths = container.querySelectorAll("path[data-location-id]");
    expect(paths).toHaveLength(263);
    const regions = valid.layers[0].regions as { location_ids: number[] }[];
    const mappedIds = regions.flatMap((r) => r.location_ids);
    expect(container.querySelectorAll("path[class*='zm-blues-']")).toHaveLength(mappedIds.length);
    const highlighted = container.querySelectorAll("path.zm-highlight");
    expect(highlighted).toHaveLength(1);
    expect(highlighted[0]).toHaveAttribute("data-location-id", "132");
    expect(paths[paths.length - 1]).toBe(highlighted[0]);
    expect(screen.getByRole("img")).toHaveAccessibleName(/Trip count by pickup zone/);
  });

  it("shows legend unit, filters, provenance ids and the disclaimer", async () => {
    render(<ZoneMap artifact={valid} />);
    await screen.findByRole("img");
    const legend = screen.getByLabelText("Legend");
    expect(legend).toHaveTextContent("0 trips");
    expect(legend).toHaveTextContent("154,000 trips");
    expect(screen.getByText("Filters: pickup_borough = Queens")).toBeInTheDocument();
    const prov = screen.getByText(/^Source:/);
    for (const id of ["run_fixture0001", "call_fixture0001", "query_fixture0001"]) {
      expect(prov).toHaveTextContent(id);
    }
    expect(screen.getByText("Shows where values are high or low; it does not show why.")).toBeInTheDocument();
  });

  it("provides a table fallback including unmapped rows", async () => {
    render(<ZoneMap artifact={valid} />);
    await screen.findByRole("img");
    const table = screen.getByRole("table");
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(1 + valid.layers[0].regions!.length + valid.unmapped.length);
    expect(within(table).getByText("Outside of NYC")).toBeInTheDocument();
    expect(within(table).getByText("No (no geometry)")).toBeInTheDocument();
  });

  it("matches the legend and table snapshot", async () => {
    render(<ZoneMap artifact={valid} />);
    await screen.findByRole("img");
    const html = [screen.getByLabelText("Legend"), screen.getByRole("table")].map((e) => e.outerHTML).join("\n");
    expect(html).toMatchSnapshot();
  });

  it.each(Object.entries(invalid))("renders only a notice for invalid fixture %s", async (_name, payload) => {
    const { container } = render(<ZoneMap artifact={payload} />);
    await screen.findByRole("alert");
    expect(screen.getByRole("alert")).toHaveTextContent("Map could not be shown");
    expect(container.querySelectorAll("path")).toHaveLength(0);
    expect(container.textContent).not.toContain("onerror");
  });
});
