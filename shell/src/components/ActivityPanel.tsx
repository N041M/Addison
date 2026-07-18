// Activity strip — the collapsible middle region (design-doc §7.1, §7.9.1).
//
// While a turn runs it shows the latest thing Addison is doing in plain words
// ("Searching the web…"), fed by `tool.activityUpdate`. It expands to the full
// list of what happened this turn ("Show what you just did" — the transparency
// counterpart to Undo). The "Undo last action" panic button lives here too, via
// RewindControls, always reachable while any change can be put back.
//
// No shimmer, no spinner theatrics — just a calm "Working…" line.

import { useState } from "react";
import type { ActivityUpdate } from "../types/protocol";
import { RewindControls } from "./RewindControls";

interface Props {
  isWorking: boolean;
  current: ActivityUpdate | null;
  activities: ActivityUpdate[];
  hasUndoableActions: boolean;
  onUndoLastAction: () => void;
  lastUndoDetail?: string | null;
  canRedo?: boolean;
  onRedoLastAction?: () => void;
  // §6.3: offered once a turn actually did something — Addison drafts a routine
  // from those steps; nothing is saved without the confirmation card.
  onProposeRoutine?: () => void;
}

export function ActivityPanel({
  isWorking,
  current,
  activities,
  hasUndoableActions,
  onUndoLastAction,
  lastUndoDetail,
  canRedo,
  onRedoLastAction,
  onProposeRoutine,
}: Props) {
  const [expanded, setExpanded] = useState(false);

  // Nothing worth showing: stay out of the way entirely.
  if (!isWorking && activities.length === 0 && !hasUndoableActions && !lastUndoDetail && !canRedo) {
    return null;
  }

  const headline = isWorking
    ? current?.label ?? "Working…"
    : activities.length > 0
      ? "Finished the steps below"
      : "Ready";

  const canExpand = activities.length > 0;

  return (
    <section
      aria-label="What Addison is doing"
      className="border-t border-line bg-paper/70 px-6 py-3"
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2.5 text-sm text-ink-soft">
          <span
            aria-hidden="true"
            className={
              "h-2 w-2 " + (isWorking ? "bg-accent" : "bg-muted/50")
            }
          />
          <span className="font-mono">{headline}</span>
        </div>

        <div className="flex items-center gap-4">
          {!isWorking && activities.length > 0 && onProposeRoutine && (
            <button
              type="button"
              onClick={onProposeRoutine}
              className="text-sm font-medium text-accent-dark hover:underline"
            >
              Save these steps as a routine
            </button>
          )}
          {canExpand && (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              aria-expanded={expanded}
              className="text-sm font-medium text-accent-dark hover:underline"
            >
              {expanded ? "Hide steps" : "Show what Addison did"}
            </button>
          )}
        </div>
      </div>

      {expanded && canExpand && (
        <ol className="mt-3 space-y-1.5 border-l-2 border-line pl-4">
          {activities.map((a, i) => (
            <li key={`${a.toolId}-${i}`} className="font-mono text-sm text-muted">
              {a.label}
            </li>
          ))}
        </ol>
      )}

      {(hasUndoableActions || lastUndoDetail || canRedo) && (
        <div className="mt-3">
          <RewindControls
            hasUndoableActions={hasUndoableActions}
            onUndoLastAction={onUndoLastAction}
            lastUndoDetail={lastUndoDetail}
            canRedo={canRedo}
            onRedoLastAction={onRedoLastAction}
          />
        </div>
      )}
    </section>
  );
}
