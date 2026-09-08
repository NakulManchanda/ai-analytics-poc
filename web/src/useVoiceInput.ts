import { useCallback, useEffect, useRef, useState } from "react";

export interface UseVoiceInputOptions {
  onTranscript: (text: string) => void;
  wsUrl?: string;
}

export interface UseVoiceInputResult {
  isListening: boolean;
  isProcessing: boolean;
  audioLevel: number;
  error: string | null;
  startListening: () => Promise<void>;
  stopListening: () => void;
  resetError: () => void;
}

function downsampleTo16k(input: Float32Array, sampleRate: number): Float32Array {
  if (sampleRate === 16000) return input;
  const ratio = sampleRate / 16000;
  const newLength = Math.round(input.length / ratio);
  const result = new Float32Array(newLength);
  for (let i = 0; i < newLength; i++) {
    const origIndex = i * ratio;
    const low = Math.floor(origIndex);
    const high = Math.min(low + 1, input.length - 1);
    const weight = origIndex - low;
    result[i] = input[low] * (1 - weight) + input[high] * weight;
  }
  return result;
}

function convertFloat32ToInt16(float32Array: Float32Array): Int16Array {
  const int16 = new Int16Array(float32Array.length);
  for (let i = 0; i < float32Array.length; i++) {
    const s = Math.max(-1, Math.min(1, float32Array[i]));
    int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return int16;
}

export function useVoiceInput({
  onTranscript,
  wsUrl,
}: UseVoiceInputOptions): UseVoiceInputResult {
  const [isListening, setIsListening] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [audioLevel, setAudioLevel] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const socketRef = useRef<WebSocket | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const animationFrameRef = useRef<number | null>(null);
  const processorNodeRef = useRef<ScriptProcessorNode | AudioWorkletNode | null>(null);
  const pcmBufferRef = useRef<number[]>([]);

  const cleanupAudio = useCallback(() => {
    if (animationFrameRef.current !== null) {
      cancelAnimationFrame(animationFrameRef.current);
      animationFrameRef.current = null;
    }
    setAudioLevel(0);

    if (processorNodeRef.current) {
      try {
        processorNodeRef.current.disconnect();
      } catch {
        // ignore
      }
      processorNodeRef.current = null;
    }

    if (analyserRef.current) {
      try {
        analyserRef.current.disconnect();
      } catch {
        // ignore
      }
      analyserRef.current = null;
    }

    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((track) => track.stop());
      mediaStreamRef.current = null;
    }

    if (audioContextRef.current) {
      try {
        void audioContextRef.current.close();
      } catch {
        // ignore
      }
      audioContextRef.current = null;
    }

    pcmBufferRef.current = [];
  }, []);

  const stopListening = useCallback(() => {
    if (!isListening) return;

    setIsListening(false);
    setIsProcessing(true);

    // 1. Tell backend to finish audio processing
    if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
      try {
        socketRef.current.send(JSON.stringify({ type: "stop" }));
      } catch {
        // ignore
      }
    }

    // 2. Shut down microphone and audio analysis
    cleanupAudio();

    // 3. Safety timeout: reset processing if server doesn't close within 3s
    setTimeout(() => {
      setIsProcessing(false);
      if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
        socketRef.current.close();
      }
    }, 3000);
  }, [cleanupAudio, isListening]);

  const startListening = useCallback(async () => {
    setError(null);

    // Determine default WebSocket URL if not supplied
    const targetWsUrl =
      wsUrl ||
      (() => {
        const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
        const host = window.location.host || "localhost:8080";
        return `${proto}//${host}/ws/voice`;
      })();

    let stream: MediaStream;
    try {
      if (!navigator?.mediaDevices?.getUserMedia) {
        throw new Error("Microphone input is not supported by your browser.");
      }
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      mediaStreamRef.current = stream;
    } catch (err: any) {
      const message =
        err?.name === "NotAllowedError" || err?.name === "PermissionDeniedError"
          ? "Microphone permission denied. Please allow microphone access in your browser."
          : err?.message || "Failed to access microphone.";
      setError(message);
      return;
    }

    try {
      const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
      if (!AudioCtx) {
        throw new Error("Web Audio API is not supported in this browser.");
      }
      const audioCtx = new AudioCtx();
      audioContextRef.current = audioCtx;

      // Connect source and analyser
      const source = audioCtx.createMediaStreamSource(stream);
      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 256;
      analyser.smoothingTimeConstant = 0.3;
      source.connect(analyser);
      analyserRef.current = analyser;

      // Animate live volume level
      const dataArray = new Uint8Array(analyser.frequencyBinCount);
      const updateLevel = () => {
        if (!analyserRef.current) return;
        analyserRef.current.getByteFrequencyData(dataArray);
        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) {
          sum += dataArray[i];
        }
        const avg = sum / dataArray.length;
        const normalized = Math.min(1, avg / 128);
        setAudioLevel(normalized);
        animationFrameRef.current = requestAnimationFrame(updateLevel);
      };
      animationFrameRef.current = requestAnimationFrame(updateLevel);

      // Open WebSocket connection
      const ws = new WebSocket(targetWsUrl);
      ws.binaryType = "arraybuffer";
      socketRef.current = ws;

      ws.onopen = () => {
        setIsListening(true);
        setIsProcessing(false);
      };

      ws.onmessage = (event) => {
        try {
          if (typeof event.data !== "string") return;
          const payload = JSON.parse(event.data);
          if (payload.type === "transcript.final" && payload.text) {
            onTranscript(payload.text);
          } else if (payload.type === "voice.completed") {
            setIsProcessing(false);
            setIsListening(false);
            cleanupAudio();
          } else if (payload.type === "voice.error") {
            setError(payload.error || "Speech transcription service error.");
            setIsProcessing(false);
            setIsListening(false);
            cleanupAudio();
            ws.close();
          }
        } catch {
          // ignore non-JSON messages
        }
      };

      ws.onerror = () => {
        setError("Voice connection error. Please ensure backend is reachable.");
        setIsListening(false);
        setIsProcessing(false);
        cleanupAudio();
      };

      ws.onclose = () => {
        setIsListening(false);
        setIsProcessing(false);
        cleanupAudio();
      };

      // Continuous chunking audio processor
      // 100ms at 16kHz = 1600 samples = 3200 bytes
      const CHUNK_SAMPLES = 1600;
      const bufferSize = 4096;
      const processor = audioCtx.createScriptProcessor(bufferSize, 1, 1);
      processorNodeRef.current = processor;

      processor.onaudioprocess = (e) => {
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        const inputData = e.inputBuffer.getChannelData(0);
        const resampled = downsampleTo16k(inputData, audioCtx.sampleRate);

        for (let i = 0; i < resampled.length; i++) {
          pcmBufferRef.current.push(resampled[i]);
        }

        while (pcmBufferRef.current.length >= CHUNK_SAMPLES) {
          const chunk = pcmBufferRef.current.splice(0, CHUNK_SAMPLES);
          const floatChunk = new Float32Array(chunk);
          const int16Chunk = convertFloat32ToInt16(floatChunk);
          ws.send(int16Chunk.buffer as ArrayBuffer);
        }
      };

      source.connect(processor);
      processor.connect(audioCtx.destination);
    } catch (err: any) {
      setError(err?.message || "Failed to initialize audio capture.");
      setIsListening(false);
      setIsProcessing(false);
      cleanupAudio();
    }
  }, [cleanupAudio, onTranscript, wsUrl]);

  useEffect(() => {
    return () => {
      cleanupAudio();
      if (socketRef.current) {
        socketRef.current.close();
        socketRef.current = null;
      }
    };
  }, [cleanupAudio]);

  const resetError = useCallback(() => {
    setError(null);
  }, []);

  return {
    isListening,
    isProcessing,
    audioLevel,
    error,
    startListening,
    stopListening,
    resetError,
  };
}
