// The Restore points modal — Settings → "All restore points" → "open"
// (docs/design-brief-dark, "Restore points modal"; prototype.html ~183–204).
//
// The one piece of floating chrome in the whole G3 surface: a scrim over the
// app and a centred 440px `panel` card. Everything inside it is REAL — the rows
// come from `snapshot.list` through useSnapshots, "save one now" is
// `snapshot.create`, and a permanent row's "Restore this one" runs the same
// two-step confirm as everywhere else (SnapshotRows owns that, so the modal and
// the Snapshots surface can never disagree about what a row may do).
//
// Closing: the scrim, the ✕, and Escape. Clicks inside never propagate to the
// scrim — a mis-click on a row must not throw the list away. Escape is handled
// by App, ahead of the "leave this surface" branch, so the first press closes
// the modal and the second returns to chat.

import { SaveSnapshotAction, SnapshotRows, snapshotsEmptyLine } from "./SnapshotsCard";
import type { SnapshotsState } from "../hooks/useSnapshots";

export function RestorePointsModal({
  connected,
  snapshots: state,
  onClose,
}: {
  connected: boolean;
  snapshots: SnapshotsState;
  onClose: () => void;
}) {
  const { snapshots, snapshotsLoaded, warning, notice } = state;
  return (
    <div
      onClick={onClose}
      className="fixed inset-0 z-40 flex animate-[fade_.2s_ease_both] items-center justify-center bg-scrim px-4"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Restore points"
        onClick={(e) => e.stopPropagation()}
        className="no-scrollbar max-h-[70vh] w-[440px] max-w-[88vw] animate-[fadeRise_.25s_ease_both] overflow-y-auto rounded-modal border border-rail bg-panel px-[22px] pb-4 pt-[18px] shadow-modal"
      >
        <div className="flex items-baseline gap-3 pb-1">
          <span className="text-[15px] tracking-display text-ink">Restore points</span>
          <span className="flex-1" />
          <SaveSnapshotAction connected={connected} snapshots={state} />
          <button
            type="button"
            onClick={onClose}
            aria-label="Close restore points"
            className="pl-1.5 text-[13px] text-disabled transition-colors hover:text-ink"
          >
            ✕
          </button>
        </div>
        <p className="m-0 mb-3.5 mt-0.5 text-[12px] leading-[1.55] text-muted">
          Addison saves one before anything risky, so you can always go back to a setup that
          worked.
        </p>

        {/* The sticky capture-failure warning and the last outcome ride here too:
            this is where "save one now" lives, so it is where the person needs to
            read that an automatic save failed. */}
        {warning && (
          <p className="m-0 mb-3 text-[12px] leading-[1.55] text-ink-soft">{warning}</p>
        )}
        {notice && <p className="m-0 mb-3 text-[12px] leading-[1.55] text-ink-soft">{notice}</p>}

        {snapshots.length === 0 ? (
          <p className="m-0 border-t border-line py-3 text-[12px] text-muted">
            {snapshotsEmptyLine(connected, snapshotsLoaded)}
          </p>
        ) : (
          <SnapshotRows connected={connected} snapshots={state} rows={snapshots} />
        )}

        <div className="border-t border-line px-0.5 pb-0.5 pt-2.5 font-mono text-[10px] text-disabled">
          everything can be undone · restores never delete your files
        </div>
      </div>
    </div>
  );
}
