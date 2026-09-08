import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WaveformVisualizer } from "./WaveformVisualizer";

describe("WaveformVisualizer", () => {
  afterEach(cleanup);

  it("renders nothing when idle and not processing", () => {
    const { container } = render(
      <WaveformVisualizer
        isListening={false}
        isProcessing={false}
        audioLevel={0}
        onStop={() => {}}
      />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders active listening state with status text, bars, and stop button", async () => {
    const onStop = vi.fn();
    render(
      <WaveformVisualizer
        isListening={true}
        isProcessing={false}
        audioLevel={0.5}
        onStop={onStop}
      />,
    );

    expect(screen.getByText("Listening… Speak your question")).toBeDefined();
    expect(screen.getByRole("region", { name: "Audio recording status" })).toBeDefined();

    const stopButton = screen.getByRole("button", { name: "Stop recording voice input" });
    expect(stopButton).toBeDefined();

    fireEvent.click(stopButton);
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it("renders processing state with distinct status text", () => {
    render(
      <WaveformVisualizer
        isListening={false}
        isProcessing={true}
        audioLevel={0}
        onStop={() => {}}
      />,
    );

    expect(screen.getByText("Processing final transcript…")).toBeDefined();
    // In processing mode, stop button is not rendered
    expect(screen.queryByRole("button", { name: "Stop recording voice input" })).toBeNull();
  });
});
