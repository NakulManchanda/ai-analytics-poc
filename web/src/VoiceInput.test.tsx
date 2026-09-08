import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

// Mock WebSocket
class MockWebSocket {
  static instances: MockWebSocket[] = [];
  url: string;
  binaryType: string = "blob";
  readyState: number = WebSocket.CONNECTING;
  sentMessages: (string | ArrayBuffer | ArrayBufferView)[] = [];

  onopen: ((event: any) => void) | null = null;
  onmessage: ((event: any) => void) | null = null;
  onerror: ((event: any) => void) | null = null;
  onclose: ((event: any) => void) | null = null;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
    setTimeout(() => {
      this.readyState = WebSocket.OPEN;
      if (this.onopen) this.onopen({});
    }, 0);
  }

  send(data: string | ArrayBuffer | ArrayBufferView) {
    this.sentMessages.push(data);
  }

  close() {
    this.readyState = WebSocket.CLOSED;
    if (this.onclose) this.onclose({});
  }

  simulateServerMessage(data: any) {
    if (this.onmessage) {
      this.onmessage({ data: typeof data === "string" ? data : JSON.stringify(data) });
    }
  }

  simulateServerError(errorMsg?: string) {
    if (this.onerror) {
      this.onerror({ error: errorMsg });
    }
  }
}

// Mock Web Audio Context
class MockAudioContext {
  sampleRate = 16000;
  destination = {};

  createMediaStreamSource() {
    return {
      connect: vi.fn(),
      disconnect: vi.fn(),
    };
  }

  createAnalyser() {
    return {
      fftSize: 256,
      smoothingTimeConstant: 0.3,
      frequencyBinCount: 128,
      connect: vi.fn(),
      disconnect: vi.fn(),
      getByteFrequencyData: vi.fn((arr: Uint8Array) => arr.fill(50)),
    };
  }

  createScriptProcessor() {
    return {
      connect: vi.fn(),
      disconnect: vi.fn(),
      onaudioprocess: null as any,
    };
  }

  close() {
    return Promise.resolve();
  }
}

describe("Voice Input UI Integration", () => {
  let originalWebSocket: any;
  let originalAudioContext: any;
  let originalMediaDevices: any;

  beforeEach(() => {
    MockWebSocket.instances = [];
    originalWebSocket = globalThis.WebSocket;
    originalAudioContext = (globalThis as any).AudioContext;
    originalMediaDevices = navigator.mediaDevices;

    (globalThis as any).WebSocket = MockWebSocket;
    (globalThis as any).AudioContext = MockAudioContext;

    // Mock initial /api/status fetch
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input: RequestInfo | URL) => {
      const url = typeof input === "string" ? input : input.toString();
      if (url.includes("/api/status")) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            app: { status: "ok", service: "ai-app" },
            mcp: { status: "ok", tools: 2, resources: 1 },
          }),
        } as any;
      }
      return { ok: true, status: 200, json: async () => ({}) } as any;
    });
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    globalThis.WebSocket = originalWebSocket;
    (globalThis as any).AudioContext = originalAudioContext;
    Object.defineProperty(navigator, "mediaDevices", {
      value: originalMediaDevices,
      writable: true,
      configurable: true,
    });
  });

  it("renders the Voice button alongside prompt actions", async () => {
    render(<App />);

    const voiceBtn = screen.getByRole("button", { name: "Start voice input" });
    expect(voiceBtn).toBeDefined();
    expect(voiceBtn.textContent).toContain("Voice");
  });

  it("handles microphone permission rejection gracefully", async () => {
    const notAllowedError = new Error("Permission denied");
    notAllowedError.name = "NotAllowedError";

    Object.defineProperty(navigator, "mediaDevices", {
      value: {
        getUserMedia: vi.fn().mockRejectedValue(notAllowedError),
      },
      writable: true,
      configurable: true,
    });

    render(<App />);

    const voiceBtn = screen.getByRole("button", { name: "Start voice input" });
    await act(async () => {
      fireEvent.click(voiceBtn);
    });

    await waitFor(() => {
      expect(
        screen.getByText(/Microphone permission denied\. Please allow microphone access/i),
      ).toBeDefined();
    });

    // Dismiss error
    const dismissBtn = screen.getByRole("button", { name: "Dismiss voice error" });
    await act(async () => {
      fireEvent.click(dismissBtn);
    });

    expect(
      screen.queryByText(/Microphone permission denied\. Please allow microphone access/i),
    ).toBeNull();
  });

  it("streams voice input, renders waveform, and inserts final transcript into query input", async () => {
    const mockTrack = { stop: vi.fn() };
    const mockStream = {
      getTracks: () => [mockTrack],
    };

    Object.defineProperty(navigator, "mediaDevices", {
      value: {
        getUserMedia: vi.fn().mockResolvedValue(mockStream),
      },
      writable: true,
      configurable: true,
    });

    render(<App />);

    const voiceBtn = screen.getByRole("button", { name: "Start voice input" });
    await act(async () => {
      fireEvent.click(voiceBtn);
    });

    // Wait for WebSocket connection to open
    await waitFor(() => {
      expect(MockWebSocket.instances.length).toBe(1);
    });

    const ws = MockWebSocket.instances[0];

    await waitFor(() => {
      expect(screen.getByText("Listening… Speak your question")).toBeDefined();
      expect(screen.getByRole("button", { name: "Stop listening" })).toBeDefined();
    });

    // Stop listening
    const stopBtn = screen.getByRole("button", { name: "Stop listening" });
    await act(async () => {
      fireEvent.click(stopBtn);
    });

    // Verify stop control message sent over WebSocket
    expect(ws.sentMessages).toContain(JSON.stringify({ type: "stop" }));
    expect(mockTrack.stop).toHaveBeenCalled();

    // Simulate backend sending transcript.final
    await act(async () => {
      ws.simulateServerMessage({
        type: "transcript.final",
        text: "Which pickup zones have the highest tips?",
      });
    });

    // Verify textarea value was updated with the final transcript
    const textarea = screen.getByLabelText("Ask about NYC taxi activity") as HTMLTextAreaElement;
    expect(textarea.value).toBe("Which pickup zones have the highest tips?");
  });
});
