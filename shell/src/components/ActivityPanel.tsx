// "Addison's work" block — the live-annotation list (design-brief-fern README §3,
// handoff §2/§4). A blocky annotation: a 2px `rule` left border wrapping ONLY the
// small-caps label and the dot list (filled fern = done, 1.5px outlined ring =
// in progress). Below the list, the underlined "Save these steps as a routine"
// link once a turn actually did something.
//
// This renders as a bare content block with no outer chrome, so it can sit inside
// the widget rail (rail open) or inline above the composer (rail hidden) — App
// chooses the slot. The "Undo last action" panic button now lives in the chat
// header; only the redo affordance and the plain undo-detail line remain here.

import type { ActivityUpdate } from "../types/protocol";

interface Props {
  isWorking: boolean;
  current: ActivityUpdate | null;
  activities: ActivityUpdate[];
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
  lastUndoDetail,
  canRedo,
  onRedoLastAction,
  onProposeRoutine,
}: Props) {
  // Nothing worth showing: stay out of the way entirely.
  if (!isWorking && activities.length === 0 && !lastUndoDetail && !canRedo) {
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
    <section aria-label="What Addison is doing">
      {steps.length > 0 && (
        // Blocky "live annotation": a 2px rule wrapping ONLY the label + list.
        <div className="border-l-2 border-rule pl-3.5">
          <p className="text-[10.5px] font-semibold uppercase tracking-[0.11em] text-faint">
            Addison's work
          </p>
          <ul className="mt-2.5 space-y-[7px]">
            {steps.map((s, i) => (
              <li key={i} className="flex items-baseline gap-2 text-[12.5px] text-ink-soft">
                <span aria-hidden="true" className="shrink-0 -translate-y-[1px]">
                  {s.done ? (
                    <span className="block h-[6px] w-[6px] rounded-pill bg-fern" />
                  ) : (
                    <span className="block h-[6px] w-[6px] rounded-pill border-[1.5px] border-fern" />
                  )}
                </span>
                <span>{s.label}</span>
              </li>
            ))}
          </ul>

          {!isWorking && activities.length > 0 && onProposeRoutine && (
            <button
              type="button"
              onClick={onProposeRoutine}
              className="mt-2.5 text-xs font-medium text-fern-deep underline underline-offset-2 hover:text-fern"
            >
              Save these steps as a routine
            </button>
          )}
        </div>
      )}

      {(canRedo || lastUndoDetail) && (
        <div className="mt-3 flex flex-col gap-1.5">
          {canRedo && onRedoLastAction && (
            <button
              type="button"
              onClick={onRedoLastAction}
              className="inline-flex w-fit items-center gap-1.5 text-[13px] font-medium text-muted hover:text-ink-soft"
            >
              <span aria-hidden="true">↻</span>
              Do it again
            </button>
          )}
          {lastUndoDetail && <p className="text-[13px] text-muted">{lastUndoDetail}</p>}
        </div>
      )}
    </section>
  );
}
