import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import {
  buildCancelledStream,
  buildConcurrentSpecialistStream,
  buildSingleAgentBaseline,
} from "./fixtures";
import { applyAgentEvents, createInitialWorkbenchState } from "./state";
import { SpecialistWorkbench } from "./SpecialistWorkbench";

afterEach(cleanup);

describe("SpecialistWorkbench E2E behavior (#233)", () => {
  it("renders two parallel specialist workers, tool failure + retry, and zero duplicates on reconnect", () => {
    const stream = buildConcurrentSpecialistStream();
    const baseline = buildSingleAgentBaseline();

    render(
      <SpecialistWorkbench
        title="Concurrent specialist run"
        question="Queens pickup concentration and hourly trends"
        events={stream}
        baselineEvents={baseline}
      />,
    );

    // Coordinator + 2 parallel specialist cards exist, no duplicates.
    const cards = document.querySelectorAll("[data-agent-card]");
    expect(cards.length).toBe(3);
    expect(document.querySelector('[data-agent-card="coordinator"]')).toHaveAttribute(
      "data-agent-status",
      "completed",
    );
    expect(document.querySelector('[data-agent-card="spatial-analyst"]')).toHaveAttribute(
      "data-agent-status",
      "completed",
    );
    expect(document.querySelector('[data-agent-card="trend-analyst"]')).toHaveAttribute(
      "data-agent-status",
      "completed",
    );

    // Expand trend-analyst tool trace and verify failed attempt + successful retry are visible.
    const trendCard = document.querySelector('[data-agent-card="trend-analyst"]') as HTMLElement;
    fireEvent.click(within(trendCard).getByRole("button", { name: /show tool trace/i }));
    expect(trendCard.querySelector('[data-tool-status="failed"]')).toHaveTextContent(
      /transient query timeout/i,
    );
    expect(trendCard.querySelector('[data-tool-status="completed"]')).toHaveTextContent(
      /succeeded/i,
    );

    // Reconnect replay of overlapping frames produces zero duplicate cards or timeline entries.
    const beforeTimelineCount = document.querySelectorAll("[data-timeline-seq]").length;
    fireEvent.click(screen.getByRole("button", { name: /simulate reconnect/i }));
    expect(document.querySelectorAll("[data-agent-card]").length).toBe(3);
    expect(document.querySelectorAll("[data-timeline-seq]").length).toBe(beforeTimelineCount);

    // Compare against single-agent baseline toggle works cleanly.
    fireEvent.click(screen.getByRole("button", { name: /single-agent baseline/i }));
    expect(document.querySelectorAll("[data-agent-card]").length).toBe(1);
    expect(document.querySelector('[data-agent-card="single-agent"]')).toBeInTheDocument();
  });

  it("handles mid-flight cancellation and reducer dedupe across reloads", () => {
    const cancelled = buildCancelledStream();
    const firstPass = applyAgentEvents(createInitialWorkbenchState(), cancelled);
    // Re-applying overlapping prefix after reload must be idempotent.
    const replayed = applyAgentEvents(firstPass, cancelled.slice(0, 5));
    expect(replayed.finalStatus).toBe("cancelled");
    expect(replayed.agentOrder).toEqual(["coordinator", "spatial-analyst", "trend-analyst"]);
    expect(replayed.agents["spatial-analyst"].status).toBe("cancelled");
    expect(replayed.agents["trend-analyst"].status).toBe("cancelled");
    expect(replayed.timeline.length).toBe(cancelled.length);

    // Interactive cancel mid-stream from step 6 (while both specialists are running).
    render(
      <SpecialistWorkbench
        title="Interactive cancel"
        question="Cancel mid-flight"
        events={buildConcurrentSpecialistStream()}
        initialStep={6}
      />,
    );
    expect(document.querySelector('[data-run-status="running"]')).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /cancel run/i }));
    expect(document.querySelector('[data-run-status="cancelled"]')).toBeInTheDocument();
    expect(document.querySelector('[data-agent-card="spatial-analyst"]')).toHaveAttribute(
      "data-agent-status",
      "cancelled",
    );
  });
});
