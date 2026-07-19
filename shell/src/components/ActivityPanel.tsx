// Activity strip — the collapsible middle region (design-doc §7.1, §7.9.1).
//
// While a turn runs it shows the latest thing Addison is doing in plain words
// ("Searching the web…"), fed by `tool.activityUpdate`. It expands to the full
// list of what happened this turn ("Show what you just did" — the transparency
// counterpart to Undo). The "Undo last action" panic button lives here too, via
// RewindControls, always reachable while any change can be put back.
//
// No shimmer, no spinner theatrics — just a calm "Working…" line.

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
  // Nothing worth showing: stay out of the way entirely.
  if (!isWorking && activities.length === 0 && !hasUndoableActions && !lastUndoDetail && !canRedo) {
    return null;
  }

  // The dot list: past steps read as done (filled fern dot); the newest step is
  // in progress (1.5px outlined ring) while Addison is still working. When there
  // are no steps yet but a turn is running, show the live headline as the single
  // in-progress line.
  const steps: { label: string; done: boolean }[] =
    activities.length > 0
      ? activities.map((a, i) => ({
          label: a.label,
          done: !(isWorking && i === activities.length - 1),
        }))
      : isWorking
        ? [{ label: current?.label ?? "Working…", done: false }]
        : [];

  return (
    <section aria-label="What Addison is doing" className="border-t border-line bg-paper px-6 py-4">
      <div className="mx-auto w-full max-w-3xl">
        {steps.length > 0 && (
          // Blocky "live annotation": a 2px rule wrapping ONLY the label + list.
          <div className="border-l-2 border-rule pl-3.5">
            <p className="text-[10.5px] font-semibold uppercase tracking-[0.1em] text-faint">
              Addison's work
            </p>
            <ul className="mt-2 space-y-1.5">
              {steps.map((s, i) => (
                <li key={i} className="flex items-start gap-2.5 text-[12.5px] text-ink-soft">
                  <span aria-hidden="true" className="mt-[6px] shrink-0">
                    {s.done ? (
                      <span className="block h-[7px] w-[7px] rounded-pill bg-fern" />
                    ) : (
                      <span className="block h-[7px] w-[7px] rounded-pill border-[1.5px] border-fern" />
                    )}
                  </span>
                  <span>{s.label}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {!isWorking && activities.length > 0 && onProposeRoutine && (
          <button
            type="button"
            onClick={onProposeRoutine}
            className="mt-3 text-[13px] font-medium text-fern-deep underline underline-offset-2 hover:text-fern"
          >
            Save these steps as a routine
          </button>
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
      </div>
    </section>
  );
}
