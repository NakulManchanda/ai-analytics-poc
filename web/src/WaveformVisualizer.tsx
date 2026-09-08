import React from "react";

interface WaveformVisualizerProps {
  isListening: boolean;
  isProcessing: boolean;
  audioLevel: number;
  onStop: () => void;
}

export const WaveformVisualizer: React.FC<WaveformVisualizerProps> = ({
  isListening,
  isProcessing,
  audioLevel,
  onStop,
}) => {
  if (!isListening && !isProcessing) {
    return null;
  }

  // 16 animated bars with heights scaling based on audioLevel
  const barCount = 16;
  const bars = Array.from({ length: barCount }, (_, index) => {
    // Generate organic wave variance around the central level
    const offset = Math.sin((index / (barCount - 1)) * Math.PI);
    const variance = (index % 3) * 0.15;
    const baseHeight = isProcessing ? 0.2 : 0.15;
    const heightPercent = Math.min(
      100,
      Math.max(15, (baseHeight + audioLevel * 0.85 * offset + variance * audioLevel) * 100),
    );
    return heightPercent;
  });

  return (
    <div className="voice-visualizer-container" role="region" aria-label="Audio recording status">
      <div className="voice-visualizer-status">
        <span className={`voice-indicator-dot ${isListening ? "active" : "processing"}`} />
        <span className="voice-status-text">
          {isListening
            ? "Listening… Speak your question"
            : "Processing final transcript…"}
        </span>
      </div>

      <div className="waveform-bars" aria-hidden="true">
        {bars.map((height, i) => (
          <div
            key={i}
            className={`waveform-bar ${isProcessing ? "processing" : ""}`}
            style={{
              height: `${height}%`,
              transition: isProcessing ? "all 0.3s ease" : "height 0.08s ease",
            }}
          />
        ))}
      </div>

      {isListening && (
        <button
          type="button"
          className="btn-voice-stop"
          onClick={onStop}
          aria-label="Stop recording voice input"
        >
          ⏹ Done Speaking
        </button>
      )}
    </div>
  );
};
