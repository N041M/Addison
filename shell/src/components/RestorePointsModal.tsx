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
//
// FOCUS IS PART OF THE FLOOR HERE, not polish. `aria-modal="true"` tells assistive
// tech the rest of the page is gone, so if focus is left behind the scrim a screen
// reader lands on rows it has been told do not exist — on the one surface a person
// opens when something has already gone wrong. So: focus moves into the dialog on
// open, Tab and Shift+Tab wrap inside it, and the opener gets focus back on every
// close path (scrim, ✕, Escape — all of them unmount this component).

import { useEffect, useRef, type KeyboardEvent } from "react";
import { SaveSnapshotAction, SnapshotRows, snapshotsEmptyLine } from "./SnapshotsCard";
import type { SnapshotsState } from "../hooks/useSnapshots";

/** Everything Tab can reach, in DOM order. No `offsetParent`/visibility filter:
 * nothing in this dialog is conditionally hidden by CSS, and such a filter would
 * silently empty the list under jsdom, where the trap is tested. */
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
  'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** The footer's undo claim, mode-scoped.
 *
 * "everything can be undone" is true under SAFE (Simple) — SAFE-2 makes a real
 * `undo()` a registration requirement for every tool above LOW. It is NOT true
 * under OPEN (Developer/Custom), where `run_command` is that rule's one explicit
 * exemption, and the app already says so two sections away in Settings ("…some
 * actions can't be undone"). A footer that contradicts the profile card is the
 * kind of quiet over-promise this floor cannot afford, so the claim follows the
 * mode. The restores half is true in every mode and never changes.
 *
 * An absent mode is treated as "safe", the same convention as `ProfileState.mode`
 * (an old core with no OPEN mode at all). */
export function footerNote(mode?: "safe" | "open"): string {
  return mode === "open"
    ? "some actions can't be undone · restores never delete your files"
    : "everything can be undone · restores never delete your files";
}

export function RestorePointsModal({
  connected,
  snapshots: state,
  mode,
  onClose,
}: {
  connected: boolean;
  snapshots: SnapshotsState;
  /**
   * The active policy mode (`profile.mode`). Only the footer's undo claim reads
   * it. Absent → treated as SAFE, per ProfileState.mode.
   */
  mode?: "safe" | "open";
  onClose: () => void;
}) {
  const { snapshots, snapshotsLoaded, warning, notice } = state;
  const dialogRef = useRef<HTMLDivElement>(null);

  // Move focus in on open; hand it back to whatever opened us on close. The
  // opener may itself be gone by then (a restore re-renders Settings), in which
  // case this is a no-op and the browser's own fallback applies.
  useEffect(() => {
    const opener = document.activeElement;
    dialogRef.current?.focus();
    return () => {
      if (opener instanceof HTMLElement && document.contains(opener)) opener.focus();
    };
  }, []);

  // The trap. Tab is handled entirely here rather than leaning on the browser's
  // sequential navigation, so the wrap is the same in a real browser and in the
  // test that proves it.
  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "Tab") return;
    const panel = dialogRef.current;
    if (!panel) return;
    // The trap leaks to EXACTLY one place: a pending permission card. It is
    // hoisted to a fixed z-50 layer above this dialog (App's SurfaceConsentLayer)
    // because it blocks the turn — and a trap that sealed it off would let a
    // mouse user answer while a keyboard user had to close this list first to
    // reach a question Addison is waiting on. Consent outranks a restore list.
    const consent = document.querySelector<HTMLElement>("[data-consent-layer]");
    const stops = [
      ...Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE)),
      ...(consent ? Array.from(consent.querySelectorAll<HTMLElement>(FOCUSABLE)) : []),
    ];
    event.preventDefault();
    if (stops.length === 0) {
      panel.focus();
      return;
    }
    const at = stops.indexOf(document.activeElement as HTMLElement);
    // Focus sitting on the dialog itself (at === -1) enters at the near end.
    const next = at === -1 ? (event.shiftKey ? stops.length - 1 : 0) : at + (event.shiftKey ? -1 : 1);
    stops[(next + stops.length) % stops.length].focus();
  }

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 z-40 flex animate-[fade_.2s_ease_both] items-center justify-center bg-scrim px-4"
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="Restore points"
        tabIndex={-1}
        onKeyDown={handleKeyDown}
        onClick={(e) => e.stopPropagation()}
        className="no-scrollbar max-h-[70vh] w-[440px] max-w-[88vw] animate-[fadeRise_.25s_ease_both] overflow-y-auto rounded-modal border border-rail bg-panel px-[22px] pb-4 pt-[18px] shadow-modal outline-none"
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
          {footerNote(mode)}
        </div>
      </div>
    </div>
  );
}
