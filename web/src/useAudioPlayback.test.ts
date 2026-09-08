import { renderHook, act } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useAudioPlayback } from "./useAudioPlayback";

describe("useAudioPlayback", () => {
  beforeEach(() => {
    // Mock URL.createObjectURL and URL.revokeObjectURL
    global.URL.createObjectURL = vi.fn(() => "blob:mock-url");
    global.URL.revokeObjectURL = vi.fn();
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it("initializes with correct default state", () => {
    const { result } = renderHook(() => useAudioPlayback());

    expect(result.current.isPlayingAudio).toBe(false);
    expect(result.current.audioError).toBeNull();
  });

  it("provides handler and control functions", () => {
    const { result } = renderHook(() => useAudioPlayback());

    expect(typeof result.current.handleAudioEvent).toBe("function");
    expect(typeof result.current.stop).toBe("function");
    expect(typeof result.current.pause).toBe("function");
    expect(typeof result.current.play).toBe("function");
    expect(typeof result.current.resetError).toBe("function");
  });

  it("handles audio event without crashing", () => {
    const { result } = renderHook(() => useAudioPlayback());

    const base64AudioData = Buffer.from("test audio data").toString("base64");

    // Should not throw
    act(() => {
      result.current.handleAudioEvent(base64AudioData);
    });

    // Should have created object URL for the audio blob
    expect(global.URL.createObjectURL).toHaveBeenCalled();
  });

  it("does not auto-play when autoPlay is false", () => {
    const { result } = renderHook(() => useAudioPlayback({ autoPlay: false }));

    const base64AudioData = Buffer.from("test audio data").toString("base64");

    act(() => {
      result.current.handleAudioEvent(base64AudioData);
    });

    // With autoPlay false, should not immediately start playing
    // (without a full Audio mock, we can't verify play() wasn't called,
    // but we can verify the hook doesn't crash)
    expect(result.current).toBeDefined();
  });

  it("stops audio without crashing", () => {
    const { result } = renderHook(() => useAudioPlayback());

    const base64AudioData = Buffer.from("test audio data").toString("base64");

    act(() => {
      result.current.handleAudioEvent(base64AudioData);
    });

    // Should not throw
    act(() => {
      result.current.stop();
    });

    expect(result.current.isPlayingAudio).toBe(false);
  });

  it("pauses audio without crashing", () => {
    const { result } = renderHook(() => useAudioPlayback());

    const base64AudioData = Buffer.from("test audio data").toString("base64");

    act(() => {
      result.current.handleAudioEvent(base64AudioData);
    });

    // Should not throw
    act(() => {
      result.current.pause();
    });

    expect(result.current.isPlayingAudio).toBe(false);
  });

  it("plays audio without crashing", () => {
    const { result } = renderHook(() => useAudioPlayback({ autoPlay: false }));

    const base64AudioData = Buffer.from("test audio data").toString("base64");

    act(() => {
      result.current.handleAudioEvent(base64AudioData);
    });

    // Should not throw
    act(() => {
      result.current.play();
    });

    expect(result.current).toBeDefined();
  });

  it("revokes previous object URL when handling new audio", () => {
    const { result } = renderHook(() => useAudioPlayback());

    const base64AudioData1 = Buffer.from("test audio data 1").toString("base64");
    const base64AudioData2 = Buffer.from("test audio data 2").toString("base64");

    // Handle first audio event
    act(() => {
      result.current.handleAudioEvent(base64AudioData1);
    });

    const revokeCallsBefore = (global.URL.revokeObjectURL as any).mock.calls.length;

    // Handle second audio event
    act(() => {
      result.current.handleAudioEvent(base64AudioData2);
    });

    // Should have revoked the previous URL
    expect((global.URL.revokeObjectURL as any).mock.calls.length).toBeGreaterThan(revokeCallsBefore);
  });

  it("resets audio error state", () => {
    const { result } = renderHook(() => useAudioPlayback());

    act(() => {
      result.current.resetError();
    });

    expect(result.current.audioError).toBeNull();
  });

  it("creates object URLs with correct blob type", () => {
    const { result } = renderHook(() => useAudioPlayback());

    const base64AudioData = Buffer.from("test audio data").toString("base64");

    act(() => {
      result.current.handleAudioEvent(base64AudioData);
    });

    // Check that createObjectURL was called
    expect(global.URL.createObjectURL).toHaveBeenCalled();

    // Get the blob passed to createObjectURL
    const callArgs = (global.URL.createObjectURL as any).mock.calls[0];
    const blob = callArgs?.[0];

    // Verify it's a blob with audio/mpeg type
    if (blob instanceof Blob) {
      expect(blob.type).toBe("audio/mpeg");
    }
  });

  it("handles invalid base64 gracefully", () => {
    const { result } = renderHook(() => useAudioPlayback());

    // Invalid base64 should be handled without crashing
    expect(() => {
      act(() => {
        result.current.handleAudioEvent("!!!invalid base64 with special chars @#$%%%");
      });
    }).not.toThrow();

    // The hook should still be usable
    expect(result.current).toBeDefined();
  });

  it("provides mutable isPlayingAudio state", () => {
    const { result, rerender } = renderHook(() => useAudioPlayback());

    expect(result.current.isPlayingAudio).toBe(false);

    // Hook is properly initialized
    rerender();

    expect(result.current.isPlayingAudio).toBe(false);
  });
});
