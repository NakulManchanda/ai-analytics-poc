import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import valid from "./fixtures/pickup-count-choropleth.v1.json";
import { pathLayerStats, ZoneMap } from "./ZoneMap";

const path = (container: HTMLElement, id: number) =>
  container.querySelector(`path[data-location-id='${id}']`) as SVGPathElement;

async function renderMap(onAsk?: (zone: string) => void, artifact: unknown = valid) {
  const view = render(<ZoneMap artifact={artifact} interactive onAskAboutZone={onAsk} />);
  await screen.findByRole("img");
  return view;
}

describe("ZoneMap interactions", () => {
  afterEach(cleanup);

  it("shows zone, borough, value and unit in a tooltip on hover and hides it on leave", async () => {
    const { container } = await renderMap();
    const tip = screen.getByRole("tooltip", { hidden: true });
    expect(tip).not.toBeVisible();
    fireEvent.pointerMove(path(container, 132), { clientX: 10, clientY: 10 });
    expect(tip).toBeVisible();
    expect(tip).toHaveTextContent("JFK Airport · Queens · 154,000 trips");
    fireEvent.pointerMove(path(container, 1), { clientX: 12, clientY: 12 });
    expect(tip).toHaveTextContent("Newark Airport · EWR · no data");
    fireEvent.pointerLeave(container.querySelector("svg")!);
    expect(tip).not.toBeVisible();
  });

  it("selects on click, shows the detail card and syncs the table row; second click clears", async () => {
    const { container } = await renderMap();
    fireEvent.click(path(container, 132));
    const card = screen.getByLabelText("Selected zone");
    expect(card).toHaveTextContent("JFK Airport");
    expect(card).toHaveTextContent("Queens");
    expect(card).toHaveTextContent("154,000 trips");
    expect(card).toHaveTextContent("1 of 12");
    expect(container.querySelector("path.zm-selected")).toBeInTheDocument();
    const selectedRows = screen.getAllByRole("row", { selected: true });
    expect(selectedRows).toHaveLength(1);
    expect(selectedRows[0]).toHaveTextContent("JFK Airport");
    fireEvent.click(path(container, 132));
    expect(container.querySelector("path.zm-selected")).toBeNull();
    expect(screen.queryAllByRole("row", { selected: true })).toHaveLength(0);
  });

  it("selects from the table row button and clears on Escape", async () => {
    const { container } = await renderMap();
    fireEvent.click(within(screen.getByRole("table")).getByRole("button", { name: "LaGuardia Airport" }));
    expect(screen.getByLabelText("Selected zone")).toHaveTextContent("2 of 12");
    expect(container.querySelector("path.zm-selected")).toBeInTheDocument();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(container.querySelector("path.zm-selected")).toBeNull();
    expect(screen.getByLabelText("Selected zone")).toHaveTextContent("Escape clears");
  });

  it("marks both zones of a shared-name region as the selected row", async () => {
    const { container } = await renderMap();
    fireEvent.click(path(container, 57));
    expect(screen.getAllByRole("row", { selected: true })[0]).toHaveTextContent("Corona");
  });

  it("changes the viewBox for borough buttons and restores it with All", async () => {
    await renderMap();
    const svg = screen.getByRole("img");
    const all = svg.getAttribute("viewBox");
    fireEvent.click(screen.getByRole("button", { name: "Staten Island" }));
    const staten = svg.getAttribute("viewBox");
    expect(staten).not.toBe(all);
    expect(screen.getByRole("button", { name: "Staten Island" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Manhattan" }));
    expect(svg.getAttribute("viewBox")).not.toBe(staten);
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    expect(svg.getAttribute("viewBox")).toBe(all);
    for (const name of ["Brooklyn", "Queens", "Bronx", "EWR"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
  });

  it("asks about the zone using the geometry name, without sending", async () => {
    const onAsk = vi.fn();
    const { container } = await renderMap(onAsk);
    fireEvent.click(path(container, 132));
    fireEvent.click(screen.getByRole("button", { name: "Ask about this zone" }));
    expect(onAsk).toHaveBeenCalledExactlyOnceWith("JFK Airport");
  });

  it("does not re-render the path layer on hover, selection, view changes or Escape", async () => {
    const { container } = await renderMap();
    const baseline = pathLayerStats.renders;
    expect(baseline).toBeGreaterThan(0);
    act(() => {
      fireEvent.pointerMove(path(container, 132), { clientX: 5, clientY: 5 });
      fireEvent.pointerMove(path(container, 138), { clientX: 6, clientY: 6 });
      fireEvent.click(path(container, 138));
      fireEvent.click(screen.getByRole("button", { name: "Queens" }));
      fireEvent.click(within(screen.getByRole("table")).getByRole("button", { name: "JFK Airport" }));
      fireEvent.keyDown(document, { key: "Escape" });
    });
    expect(pathLayerStats.renders).toBe(baseline);
  });

  it("offers no interaction controls when not interactive", async () => {
    const { container } = render(<ZoneMap artifact={valid} />);
    await screen.findByRole("img");
    expect(screen.queryByRole("group", { name: "Map view" })).toBeNull();
    expect(screen.queryByRole("tooltip", { hidden: true })).toBeNull();
    expect(within(screen.getByRole("table")).queryAllByRole("button")).toHaveLength(0);
    fireEvent.click(path(container, 132));
    expect(container.querySelector("path.zm-selected")).toBeNull();
  });

  it("omits null run and tool call ids from the provenance line", async () => {
    const live = {
      ...valid,
      evidence: { ...valid.evidence, run_id: null, tool_call_id: null, tool_name: "map_taxi_zones" },
    };
    render(<ZoneMap artifact={live} />);
    await screen.findByRole("img");
    const prov = screen.getByText(/^Source:/);
    expect(prov).toHaveTextContent("query query_fixture0001");
    expect(prov.textContent).not.toMatch(/null|run |tool call/);
  });

  it("assigns distinct ranks and React keys to same-named zones with different values", async () => {
    const splitCorona = {
      ...valid,
      layers: [
        {
          ...valid.layers[0],
          regions: [
            { label: "Corona", location_ids: [56], value: 264 },
            { label: "Corona", location_ids: [57], value: 10 },
          ],
        },
      ],
    };
    const { container } = await renderMap(undefined, splitCorona);
    fireEvent.click(path(container, 56));
    expect(screen.getByLabelText("Selected zone")).toHaveTextContent("1 of 2");
    fireEvent.click(path(container, 57));
    expect(screen.getByLabelText("Selected zone")).toHaveTextContent("2 of 2");
  });
});
