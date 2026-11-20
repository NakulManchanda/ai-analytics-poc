import { useState } from "react";
import {
  buildCancelledStream,
  buildConcurrentSpecialistStream,
  buildSingleAgentBaseline,
} from "./fixtures";
import { SpecialistWorkbench } from "./SpecialistWorkbench";

type DemoScenarioId = "concurrent" | "cancelled";

export function WorkbenchDemo() {
  const [scenario, setScenario] = useState<DemoScenarioId>("concurrent");
  const concurrentEvents = buildConcurrentSpecialistStream();
  const baselineEvents = buildSingleAgentBaseline();
  const cancelledEvents = buildCancelledStream();

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", color: "#0f172a" }}>
      <nav
        aria-label="Workbench demo navigation"
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "12px 20px",
          borderBottom: "1px solid #e2e8f0",
        }}
      >
        <div>
          <strong>AI Analytics · Specialist Workbench Demo</strong>
          {" · "}
          <button type="button" onClick={() => setScenario("concurrent")}>
            Concurrent Specialists (Retry + Artifacts)
          </button>{" "}
          <button type="button" onClick={() => setScenario("cancelled")}>
            Mid-Flight Cancellation
          </button>
        </div>
        <a href="/">Back to live app</a>
      </nav>

      {scenario === "concurrent" ? (
        <SpecialistWorkbench
          key="concurrent"
          title="Queens Pickup Concentration & Temporal Fare Patterns"
          question="Where are Queens taxi pickups concentrated in Jan 2024, and how do hourly volumes and weekday/weekend fares compare?"
          events={concurrentEvents}
          baselineEvents={baselineEvents}
        />
      ) : (
        <SpecialistWorkbench
          key="cancelled"
          title="Mid-Flight Cancellation Stream"
          question="Demonstrates truthful cancellation of active specialist workers."
          events={cancelledEvents}
        />
      )}
    </main>
  );
}
