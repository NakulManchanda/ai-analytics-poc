import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { canonicalKey, clearZoneMapCache, fetchZoneMap, MapRequestError } from "./api";

const request = { zone_role: "pickup", measure: "trip_count" } as const;

describe("fetchZoneMap", () => {
  beforeEach(() => clearZoneMapCache());
  afterEach(() => vi.unstubAllGlobals());

  it("canonicalises filter order so equal requests share a key", () => {
    const a = canonicalKey({ ...request, filters: { pickup_hour_max: 9, pickup_week_part: "weekend" } });
    const b = canonicalKey({ ...request, filters: { pickup_week_part: "weekend", pickup_hour_max: 9 } });
    expect(a).toBe(b);
    expect(canonicalKey(request)).toBe(canonicalKey({ ...request, filters: {} }));
  });

  it("posts JSON once and serves repeats from the cache", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ schema: "map_artifact" }) });
    vi.stubGlobal("fetch", fetchMock);
    await fetchZoneMap(request);
    await fetchZoneMap({ ...request });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/map/zones",
      expect.objectContaining({ method: "POST", body: canonicalKey(request) }),
    );
  });

  it("shows the server's fixed message and a generic one otherwise", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce({
        ok: false,
        status: 422,
        json: async () => ({ detail: { code: "invalid_request", message: "The map request was rejected by the data tool." } }),
      }),
    );
    await expect(fetchZoneMap(request)).rejects.toMatchObject({
      message: "The map request was rejected by the data tool.",
      status: 422,
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValueOnce({ ok: false, status: 422, json: async () => ({ detail: [{ loc: ["body"] }] }) }),
    );
    await expect(fetchZoneMap(request)).rejects.toThrow("Map request failed");
    vi.stubGlobal("fetch", vi.fn().mockRejectedValueOnce(new TypeError("network")));
    await expect(fetchZoneMap(request)).rejects.toBeInstanceOf(MapRequestError);
  });

  it("passes the abort signal through and does not cache aborted requests", async () => {
    const fetchMock = vi.fn().mockImplementation(
      (_url: string, init: RequestInit) =>
        new Promise((_resolve, reject) => {
          init.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError")));
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const ctl = new AbortController();
    const pending = fetchZoneMap(request, ctl.signal);
    ctl.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    fetchMock.mockResolvedValueOnce({ ok: true, json: async () => ({}) });
    await fetchZoneMap(request);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
