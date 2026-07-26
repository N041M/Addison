// "Addison's work" — the live step list at the top of the right rail (DARK
// direction; docs/design-brief-dark, "Right rail"). A 2px `rail` left rule holds
// an 11px section label over the steps: a 5px dot and a line of 12px text each,
// the current step's dot blinking in `ink`, finished steps dimmed to `muted`.
// Below the rule sits the accent "Save as routine" action, and the quiet
// undo-detail / redo affordances.
//
// It renders as a bare content block with no outer chrome, so it can sit in the
// widget rail (rail open) or inline above the composer (rail hidden) — App picks
// the slot. The "Undo last action" control lives in the header; only the redo
// affordance and the plain undo-detail line are here.

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

  // Past steps read as done; the newest is in progress while Addison is still
  // working. With no steps yet but a turn running, the live headline is the
  // single in-progress line.
  const steps: { label: string; detail?: string; done: boolean }[] =
    activities.length > 0
      ? activities.map((a, i) => ({
          label: a.label,
          detail: a.detail,
          done: !(isWorking && i === activities.length - 1),
        }))
      : isWorking
        ? [{ label: current?.label ?? "Working…", detail: current?.detail, done: false }]
        : [];

  return (
    <section aria-label="What Addison is doing" className="shrink-0">
      {steps.length > 0 && (
        <div className="pb-5">
          <div className="border-l-2 border-rail pl-3.5">
            <p
              data-scramble="640"
              className="m-0 text-[11px] font-medium tracking-[.04em] text-faint"
            >
              Addison's work
            </p>
            <ul className="mt-[11px] flex list-none flex-col gap-[9px] p-0 text-[12px]">
              {steps.map((s, i) => (
                <li
                  key={i}
                  className={
                    "flex animate-[fadeRise_.3s_ease_both] items-start gap-2 " +
                    (s.done ? "text-muted" : "text-ink")
                  }
                >
                  <span
                    aria-hidden="true"
                    className={
                      "mt-[6px] h-[5px] w-[5px] shrink-0 rounded-full " +
                      (s.done
                        ? "bg-disabled"
                        : "animate-[blink_1.1s_step-start_infinite] bg-ink")
                    }
                  />
                  <span className="min-w-0">
                    {s.label}
                    {s.detail && (
                      // What the step is reaching — the site, for a page read. A
                      // machine fact, so mono. It sits inside the 2px rule because
                      // it is part of the same live annotation: Addison telling you
                      // what it is doing, not something you act on.
                      //
                      // It WRAPS rather than truncating, which looks worse and is
                      // the point: an address is read from the right — the last
                      // labels are the ones that say whose site this really is — so
                      // an ellipsis would hide the half that matters. The core caps
                      // the string, so this can never be more than a line or two.
                      //
                      // `text-muted`, not `text-faint`: measured against the app
                      // background at this size, faint falls under the 4.5:1 AA
                      // floor for small text (design-doc §7.1 accessibility,
                      // readers of 54 and 68) while muted clears it and still reads
                      // a step below the label. A line nobody can read is not
                      // visibility.
                      <span className="mt-0.5 block break-all font-mono text-[10px] text-muted">
                        {s.detail}
                      </span>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          </div>

          {!isWorking && activities.length > 0 && onProposeRoutine && (
            <button
              type="button"
              data-scramble="720"
              onClick={onProposeRoutine}
              className="mt-3 pl-4 text-left text-[12px] text-accent transition-colors hover:text-ink max-md:min-h-[44px]"
            >
              Save as routine
            </button>
          )}
        </div>
      )}

      {(canRedo || lastUndoDetail) && (
        <div className="flex flex-col gap-1.5 pb-5 pl-4">
          {canRedo && onRedoLastAction && (
            <button
              type="button"
              onClick={onRedoLastAction}
              className="w-fit text-left text-[12px] text-accent transition-colors hover:text-ink max-md:min-h-[44px]"
            >
              Do it again
            </button>
          )}
          {lastUndoDetail && <p className="m-0 text-[12px] text-muted">{lastUndoDetail}</p>}
        </div>
      )}
    </section>
  );
}
