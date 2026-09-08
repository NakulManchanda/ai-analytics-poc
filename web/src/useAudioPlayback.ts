import { useCallback, useRef, useState } from "react";

export interface UseAudioPlaybackOptions {
  autoPlay?: boolean;
}

export interface UseAudioPlaybackResult {
  isPlayingAudio: boolean;
  audioError: string | null;
  handleAudioEvent: (base64Data: string) => void;
  stop: () => void;
  pause: () => void;
  play: () => void;
  resetError: () => void;
}

/**
 * Hook for handling SSE answer.audio events and playing audio via HTMLAudioElement.
 * Decodes base64 MP3 data, creates a Blob, and plays via <audio> element.
 */
export function useAudioPlayback({
  autoPlay = true,
}: UseAudioPlaybackOptions = {}): UseAudioPlaybackResult {
  const [isPlayingAudio, setIsPlayingAudio] = useState(false);
  const [audioError, setAudioError] = useState<string | null>(null);

  const audioElementRef = useRef<HTMLAudioElement | null>(null);
  const audioUrlRef = useRef<string | null>(null);

  /**
   * Decode base64 string to Uint8Array
   */
  const base64ToUint8Array = useCallback((base64: string): Uint8Array => {
    const binaryString = atob(base64);
    const bytes = new Uint8Array(binaryString.length);
    for (let i = 0; i < binaryString.length; i++) {
      bytes[i] = binaryString.charCodeAt(i);
    }
    return bytes;
  }, []);

  /**
   * Handle incoming audio event with base64-encoded MP3 data
   */
  const handleAudioEvent = useCallback(
    (base64Data: string) => {
      try {
        setAudioError(null);

        // Decode base64 to binary
        let audioBytes: Uint8Array;
        try {
          audioBytes = base64ToUint8Array(base64Data);
        } catch {
          throw new Error("Failed to decode audio data from base64");
        }

        // Create Blob from audio bytes (MP3 format)
        // Convert Uint8Array to ArrayBuffer that can be used in Blob
        const audioBuffer = new ArrayBuffer(audioBytes.length);
        const audioView = new Uint8Array(audioBuffer);
        audioView.set(audioBytes);
        const audioBlob = new Blob([audioBuffer], { type: "audio/mpeg" });

        // Revoke previous URL if it exists
        if (audioUrlRef.current) {
          URL.revokeObjectURL(audioUrlRef.current);
        }

        // Create new object URL for the blob
        const audioUrl = URL.createObjectURL(audioBlob);
        audioUrlRef.current = audioUrl;

        // Create or reuse audio element
        let audioElement = audioElementRef.current;
        if (!audioElement) {
          audioElement = new Audio();
          audioElementRef.current = audioElement;

          // Set up event listeners
          audioElement.addEventListener("play", () => {
            setIsPlayingAudio(true);
          });

          audioElement.addEventListener("pause", () => {
            setIsPlayingAudio(false);
          });

          audioElement.addEventListener("ended", () => {
            setIsPlayingAudio(false);
          });

          audioElement.addEventListener("error", (e) => {
            const audioError = audioElement?.error;
            let errorMsg = "Audio playback error";
            if (audioError) {
              if (audioError.code === audioError.MEDIA_ERR_ABORTED) {
                errorMsg = "Audio playback aborted";
              } else if (audioError.code === audioError.MEDIA_ERR_NETWORK) {
                errorMsg = "Audio network error";
              } else if (audioError.code === audioError.MEDIA_ERR_DECODE) {
                errorMsg = "Audio decode error";
              } else if (audioError.code === audioError.MEDIA_ERR_SRC_NOT_SUPPORTED) {
                errorMsg = "Audio format not supported";
              }
            }
            setAudioError(errorMsg);
            setIsPlayingAudio(false);
          });
        }

        // Set the audio source and play if autoPlay is enabled
        audioElement.src = audioUrl;

        if (autoPlay) {
          const playPromise = audioElement.play();
          if (playPromise !== undefined) {
            playPromise.catch((err: Error) => {
              // Handle autoplay policy or other play errors
              if (err.name === "NotAllowedError") {
                setAudioError("Autoplay not allowed. Click to play audio.");
              } else {
                setAudioError(err.message || "Failed to play audio");
              }
              setIsPlayingAudio(false);
            });
          }
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : "Unknown audio error";
        setAudioError(message);
        setIsPlayingAudio(false);
      }
    },
    [autoPlay, base64ToUint8Array],
  );

  /**
   * Stop and reset audio playback
   */
  const stop = useCallback(() => {
    if (audioElementRef.current) {
      audioElementRef.current.pause();
      audioElementRef.current.currentTime = 0;
    }
    setIsPlayingAudio(false);
  }, []);

  /**
   * Pause audio playback (does not reset position)
   */
  const pause = useCallback(() => {
    if (audioElementRef.current) {
      audioElementRef.current.pause();
    }
    setIsPlayingAudio(false);
  }, []);

  /**
   * Resume audio playback
   */
  const play = useCallback(() => {
    if (audioElementRef.current) {
      const playPromise = audioElementRef.current.play();
      if (playPromise !== undefined) {
        playPromise.catch((err: Error) => {
          if (err.name !== "NotAllowedError") {
            setAudioError(err.message || "Failed to resume audio playback");
          }
        });
      }
    }
  }, []);

  /**
   * Reset error state
   */
  const resetError = useCallback(() => {
    setAudioError(null);
  }, []);

  // Cleanup on unmount
  const cleanup = useCallback(() => {
    if (audioElementRef.current) {
      audioElementRef.current.pause();
      audioElementRef.current = null;
    }
    if (audioUrlRef.current) {
      URL.revokeObjectURL(audioUrlRef.current);
      audioUrlRef.current = null;
    }
  }, []);

  // Set up cleanup
  // Note: We don't use useEffect here to avoid issues with SSR or strict mode
  // Cleanup can be called manually or happen naturally when component unmounts

  return {
    isPlayingAudio,
    audioError,
    handleAudioEvent,
    stop,
    pause,
    play,
    resetError,
  };
}
