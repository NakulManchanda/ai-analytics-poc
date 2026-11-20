import { useState } from "react";
import { buildExperimentScenarios } from "./experiments";
import { buildCancelledStream } from "./fixtures";
import { SpecialistWorkbench } from "./SpecialistWorkbench";

export function WorkbenchDemo() {
  const scenarios = buildExperimentScenarios();
  const [selectedId, setSelectedId] = useState<string>("airport_anomaly");
  const activeScenario = scenarios.find((s) => s.id === selectedId);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", color: "#0f172a" }}>
      <nav
        aria-label="Workbench demo navigation"
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 12,
          flexWrap: "wrap",
          padding: "12px 20px",
          borderBottom: "1px solid #e2e8f0",
        }}
      >
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
          <strong>AI Analytics · Specialist Workbench Demo</strong>
          {scenarios.map((sc) => (
            <button
              key={sc.id}
              type="button"
              aria-pressed={selectedId === sc.id}
              onClick={() => setSelectedId(sc.id)}
            >
              {sc.label}
            </button>
          ))}
          <button
            type="button"
            aria-pressed={selectedId === "cancelled"}
            onClick={() => setSelectedId("cancelled")}
          >
            Mid-Flight Cancellation
          </button>
        </div>
        <a href="/">Back to live app</a>
      </nav>

      {selectedId === "cancelled" || !activeScenario ? (
        <SpecialistWorkbench
          key="cancelled"
          title="Mid-Flight Cancellation Stream"
          question="Demonstrates truthful cancellation of active specialist workers."
          events={buildCancelledStream()}
        />
      ) : (
        <SpecialistWorkbench
          key={activeScenario.id}
          title={activeScenario.label}
          question={activeScenario.question}
          events={activeScenario.events}
          baselineEvents={activeScenario.baselineEvents}
        />
      )}
    </main>
  );
}
