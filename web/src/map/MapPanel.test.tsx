import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearZoneMapCache } from "./api";
import valid from "./fixtures/pickup-count-choropleth.v1.json";
import { MapPanel } from "./MapPanel";

const ok = (body: unknown) => ({ ok: true, json: async () => body });
const bodies = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls.map((c) => JSON.parse((c[1] as RequestInit).body as string));

function setup(props: Partial<React.ComponentProps<typeof MapPanel>> = {}) {
  const onAsk = vi.fn();
  render(<MapPanel onAskAboutZone={onAsk} onClose={() => {}} {...props} />);
  return { onAsk };
}

describe("MapPanel", () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    clearZoneMapCache();
    fetchMock = vi.fn().mockImplementation(async () => ok(valid));
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it("fetches and renders a pickup trip-count map on open", async () => {
    setup();
    expect(await screen.findByRole("img")).toHaveAccessibleName(/Trip count by pickup zone/);
    expect(bodies(fetchMock)).toEqual([{ zone_role: "pickup", measure: "trip_count", filters: {} }]);
  });

  it("refetches on a control change, serves repeats from cache, and debounces rapid changes", async () => {
    setup();
    await screen.findByRole("img");
    const measure = screen.getByLabelText("Measure");
    fireEvent.change(measure, { target: { value: "average_fare" } });
    fireEvent.change(measure, { target: { value: "average_tip" } });
    fireEvent.change(measure, { target: { value: "tip_rate" } });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(bodies(fetchMock)[1].measure).toBe("tip_rate");
    fireEvent.change(measure, { target: { value: "trip_count" } });
    await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("sends week part and hour range filters", async () => {
    setup();
    await screen.findByRole("img");
    fireEvent.change(screen.getByLabelText("Week part"), { target: { value: "weekend" } });
    fireEvent.change(screen.getByLabelText("From hour"), { target: { value: "6" } });
    fireEvent.change(screen.getByLabelText("To hour"), { target: { value: "9" } });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(bodies(fetchMock)[1].filters).toEqual({ pickup_week_part: "weekend", pickup_hour_min: 6, pickup_hour_max: 9 });
  });

  it("aborts a superseded in-flight request", async () => {
    const signals: AbortSignal[] = [];
    fetchMock.mockImplementation(
      (_u: string, init: RequestInit) =>
        new Promise((resolve, reject) => {
          signals.push(init.signal as AbortSignal);
          init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
          if (signals.length > 1) resolve(ok(valid));
        }),
    );
    setup();
    await waitFor(() => expect(signals).toHaveLength(1));
    fireEvent.click(screen.getByRole("button", { name: "Dropoff zones" }));
    await waitFor(() => expect(signals).toHaveLength(2));
    expect(signals[0].aborted).toBe(true);
    expect(await screen.findByRole("img")).toBeInTheDocument();
  });

  it("shows the server message on error and recovers on retry", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 502,
      json: async () => ({ detail: { code: "mcp_unavailable", message: "The data service is unavailable." } }),
    });
    setup();
    expect(await screen.findByRole("alert")).toHaveTextContent("The data service is unavailable.");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("img")).toBeInTheDocument();
  });

  it("shows only a notice for an invalid artifact", async () => {
    fetchMock.mockResolvedValue(ok({ ...valid, schema: "something_else" }));
    setup();
    expect(await screen.findByRole("alert")).toHaveTextContent("Map could not be shown");
    expect(document.querySelectorAll("path")).toHaveLength(0);
  });

  it("builds the chat prompt from the geometry zone name via Ask about this zone", async () => {
    const { onAsk } = setup();
    await screen.findByRole("img");
    fireEvent.click(document.querySelector("path[data-location-id='132']")!);
    fireEvent.click(screen.getByRole("button", { name: "Ask about this zone" }));
    expect(onAsk).toHaveBeenCalledExactlyOnceWith("Tell me more about taxi activity in the JFK Airport zone.");
  });

  it("keeps the original filter scope on 422 and drops chat filters only when asked", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 422,
      json: async () => ({ detail: { code: "invalid_request", message: "The map request was rejected by the data tool." } }),
    });
    setup({
      launch: {
        request: {
          zone_role: "dropoff",
          measure: "median_fare",
          filters: { pickup_week_part: "weekday", pickup_hour_min: 7, vendor: "CMT" },
        },
        dropped: false,
      },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("The map request was rejected by the data tool.");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/Also applying from the chat run: vendor = CMT/)).toBeInTheDocument();
    expect(screen.queryByText("Some filters not applied.")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Drop chat filters and retry" }));
    expect(await screen.findByRole("img")).toBeInTheDocument();
    expect(screen.getByLabelText("Measure")).toHaveValue("median_fare");
    expect(screen.getByRole("button", { name: "Dropoff zones" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("Week part")).toHaveValue("weekday");
    expect(screen.getByLabelText("From hour")).toHaveValue("7");
    const sent = bodies(fetchMock);
    expect(sent[0].filters).toEqual({ vendor: "CMT", pickup_week_part: "weekday", pickup_hour_min: 7 });
    expect(sent[1].filters).toEqual({ pickup_week_part: "weekday", pickup_hour_min: 7 });
    expect(screen.queryByText(/Also applying from the chat run/)).toBeNull();
    expect(screen.getByText("Some filters not applied.")).toBeInTheDocument();
  });

  it("does not request when the hour range is inverted", async () => {
    setup();
    await screen.findByRole("img");
    fireEvent.change(screen.getByLabelText("From hour"), { target: { value: "12" } });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    fireEvent.change(screen.getByLabelText("To hour"), { target: { value: "3" } });
    expect(await screen.findByText("The start hour is after the end hour.")).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2); // initial + From=12 only
  });
});
