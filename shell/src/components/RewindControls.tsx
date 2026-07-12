// Stop / Retry controls — engineering-spec §7.9.1.
// Stop is the first line of defense: halt a tool call before it finishes, not
// just clean up after. It replaces the send button while the agent is working.
// Retry regenerates the last response without touching anything before it.

interface Props {
  isWorking: boolean;
  onStop: () => void;       // cancels the in-progress tool call immediately
  onRetry: () => void;      // regenerate last response, no undo needed
}

export function RewindControls({ isWorking, onStop, onRetry }: Props) {
  if (isWorking) {
    return (
      <button className="stop-btn" onClick={onStop}>
        Stop
      </button>
    );
  }
  return (
    <button className="retry-btn" onClick={onRetry}>
      Retry
    </button>
  );
}
