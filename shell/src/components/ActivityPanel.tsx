// Activity strip — shows live tool calls and hosts the panic button (design-doc §7.1, §7.9.1).
// "Undo last action" lives HERE (always visible while undoable actions exist),
// not buried in Settings — panic buttons belong where the panic is.

import type { ActivityUpdate } from "../types/protocol";

interface Props {
  activity: ActivityUpdate | null;      // e.g. "Searching the web…"
  hasUndoableActions: boolean;
  onUndoLastAction: () => void;         // action rewind, §7.9 mechanism 2
  onShowWhatYouDid: () => void;         // plain-language activity log
}

export function ActivityPanel({ activity, hasUndoableActions, onUndoLastAction, onShowWhatYouDid }: Props) {
  return (
    <aside className="activity-panel">
      {activity && <div className="activity-live">{activity.label}</div>}
      {hasUndoableActions && (
        <button className="undo-btn" onClick={onUndoLastAction}>
          Undo last action
        </button>
      )}
      <button className="show-activity-btn" onClick={onShowWhatYouDid}>
        Show what you just did
      </button>
    </aside>
  );
}
