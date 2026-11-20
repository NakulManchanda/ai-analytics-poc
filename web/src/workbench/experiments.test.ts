import { describe, expect, it } from "vitest";
import { parseArtifactEnvelope, synthesizeArtifacts } from "../artifacts/envelope";
import { buildExperimentScenarios } from "./experiments";
import { applyAgentEvents, createInitialWorkbenchState } from "./state";

describe("User-facing Agent UX experiment scenarios (#236)", () => {
  it("validates all 4 NYC Taxi scenarios and the invalid-date control", () => {
    const scenarios = buildExperimentScenarios();
    expect(scenarios.map((s) => s.id)).toEqual([
      "borough_pickups",
      "year_boundary_guard",
      "airport_anomaly",
      "weekday_weekend_linked",
      "invalid_date_control",
    ]);

    for (const sc of scenarios) {
      const state = applyAgentEvents(createInitialWorkbenchState(), sc.events);
      const artifacts = state.artifactOrder.map((id) => state.artifacts[id]);

      for (const art of artifacts) {
        expect(parseArtifactEnvelope(art).ok).toBe(true);
      }
      const synthesis = synthesizeArtifacts(artifacts);
      expect(synthesis.consistent).toBe(true);

      if (sc.id === "year_boundary_guard") {
        expect(state.finalStatus).toBe("completed");
        expect(artifacts.length).toBe(0);
        expect(state.agents["trend-analyst"].summary).toMatch(/2023 data is unavailable/i);
      } else if (sc.id === "invalid_date_control") {
        expect(state.finalStatus).toBe("failed");
        expect(artifacts.length).toBe(0);
        expect(state.agents["trend-analyst"].status).toBe("failed");
      } else {
        expect(state.finalStatus).toBe("completed");
        expect(artifacts.length).toBeGreaterThan(0);
      }
    }
  });
});
