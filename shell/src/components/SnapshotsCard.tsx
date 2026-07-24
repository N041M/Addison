// Restore points — the Settings face of GLOBAL FLOOR G3 (guaranteed rollback).
//
// The story this card exists for (amendment §1): someone asked their assistant
// to make things cheaper, it broke their setup, and the rewind didn't work. So
// the copy here is written for the moment AFTER something has gone wrong, for a
// reader who is 54 or 68 and is not enjoying themselves. Plain words, no jargon,
// and the target of "Restore" is always named before the click — a recovery
// button you press blind isn't a recovery, it's a second gamble.
//
// Fern shape rule (docs/design-brief-fern, contract §11.2): the restore button is
// rounded and fern-filled, because it is yours to act on and because it is a
// RECOVERY. It is never the danger token — that token is for errors, and going
// back to a setup that worked is the opposite of a destructive act. The
// "Permanent" mark on a G4 anchor is blocky (square, 2px left rule, small caps),
// because it is Addison telling you something about the record; there is no
// control there to round off.

import { useState } from "react";
import type { SnapshotsState } from "../hooks/useSnapshots";
import { GENESIS_LABEL } from "../ipc/client";

/** Frozen consequence copy (contract §11.2, on §11.3 item 12's byte-for-byte
 * list). This sentence lives on THIS side of the wire only, and deliberately so:
 * it is what the card shows BEFORE the click, so it is future tense. The core has
 * its own past-tense counterpart for the sentence shown AFTER a restore lands
 * (`_KEYS_UNTOUCHED` in snapshot_manager.py — "…weren't touched"). Two different
 * sentences for two different moments, so neither side asserts the other's; the
 * only assertion of this one is in snapshots.test.ts. Say what comes back AND
 * what doesn't, so nobody hesitates over whether restoring costs them their
 * conversations. */
const CONSEQUENCE =
  "Your settings, services, notes, widgets and routines go back to how they were. " +
  "Your chats and your saved keys aren't touched.";

/** Appended when the target is the very first restore point: going back there
 * throws away everything set up since install, which the base sentence's "go
 * back to how they were" does not convey on its own. */
const GENESIS_CONSEQUENCE =
  "This is Addison as it was first installed, so your services, notes, widgets and routines are cleared.";

function formatWhen(createdAt: number): string {
  try {
    return new Date(createdAt * 1000).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}

/** The card's `action`-slot control, outlined rather than filled — saving a
 * restore point is a quiet housekeeping act beside the card's real primary. */
export function SaveSnapshotButton({
  connected,
  snapshots: state,
}: {
  connected: boolean;
  snapshots: SnapshotsState;
}) {
  return (
    <button
      type="button"
      disabled={!connected || state.busy}
      onClick={() => void state.handleCreateSnapshot()}
      className="shrink-0 rounded-sm border border-line bg-transparent px-3 py-1.5 text-xs font-medium text-ink-soft hover:border-muted disabled:opacity-50"
    >
      Save a restore point now
    </button>
  );
}

export function SnapshotsCard({
  connected,
  snapshots: state,
}: {
  connected: boolean;
  snapshots: SnapshotsState;
}) {
  // Held until the person confirms or backs out. Inline, never window.confirm() —
  // a native dialog can't carry the consequence copy and can't be styled to say
  // calmly that this is a recovery.
  const [confirming, setConfirming] = useState(false);
  // The permanent row whose per-row "Restore this one" is mid-confirm, if any.
  // Same inline two-step idiom as the one-action restore above.
  const [restoreConfirmId, setRestoreConfirmId] = useState<string | null>(null);

  const {
    snapshots,
    snapshotsLoaded,
    lastWorkingId,
    lastWorkingLabel,
    lastWorkingProfileChange,
    warning,
    notice,
    busy,
    handleRestoreLastWorking,
    handleRestoreSnapshot,
    handleDeleteSnapshot,
  } = state;

  const target = snapshots.find((s) => s.id === lastWorkingId);
  const targetName = lastWorkingLabel ?? target?.reasonLabel;
  const canRestore = connected && Boolean(targetName);

  return (
    <div>
      {/* The core's sticky notice that an automatic restore point couldn't be
          saved. It stays until the person saves one themselves. */}
      {warning && <p className="mb-3 text-fine leading-relaxed text-ink-soft">{warning}</p>}

      {/* Name the target, then offer the button. Never the other way round. */}
      {canRestore && targetName && (
        <div className="mb-3">
          <p className="text-meta text-ink-soft">
            Going back to <span className="font-semibold text-ink">{targetName}</span>
            {/* `text-muted`, not `text-faint`: measured against `bg-paper`, faint is
                2.5:1 in light and 4.0:1 in dark, both under the 4.5:1 AA floor — and
                this is 10.5px, for readers of 54 and 68 (design-doc §7.1). Muted
                measures 4.7:1 and 6.3:1 and still sits below the label, so the
                hierarchy survives. This is the line that says WHICH setup the button
                is about to restore; unreadable is not an option on it. */}
            {target && (
              <span className="ml-1.5 font-mono text-label text-muted">
                {formatWhen(target.createdAt)}
              </span>
            )}
          </p>
          {!confirming && (
            <button
              type="button"
              disabled={busy}
              onClick={() => setConfirming(true)}
              className="mt-2.5 rounded-sm bg-fern px-4 py-2 text-meta font-semibold text-on-accent hover:bg-fern-deep disabled:opacity-50 max-md:min-h-[44px]"
            >
              Restore to the last working state
            </button>
          )}
          {confirming && (
            <div className="mt-3 rounded-card bg-fern-tint px-[15px] py-[13px]">
              <p className="text-fine leading-relaxed text-ink-soft">
                {CONSEQUENCE}
                {/* Appended, never substituted: a restore can move you between
                    profiles — and so between how freely Addison may act — and
                    the sentence above never said so. */}
                {lastWorkingProfileChange && ` ${lastWorkingProfileChange}`}
                {targetName === GENESIS_LABEL && ` ${GENESIS_CONSEQUENCE}`}
              </p>
              <div className="mt-2.5 flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    setConfirming(false);
                    void handleRestoreLastWorking();
                  }}
                  className="rounded-pill bg-fern px-[18px] py-[7px] text-xs font-semibold text-on-accent hover:bg-fern-deep disabled:opacity-50"
                >
                  Restore
                </button>
                <button
                  type="button"
                  onClick={() => setConfirming(false)}
                  className="text-xs font-medium text-ink-soft hover:text-muted"
                >
                  Not now
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Restore points exist, but the one-action button has no target — G3's two
          honest silences. Without a line here the card lists restore points with
          no button and no reason, and the reader most in need of the floor reads
          that silence as the floor being broken. So Addison names which silence
          this is. Same quiet `text-meta text-muted` idiom (and AA reasoning) as
          the empty-state copy below — this is Addison telling you something, not
          a control, so it is one <p>, no button, no border.

          Imprecision accepted here: the core's 'unreadable' walk outcome is
          indistinguishable from 'identical' on this wire — both arrive as "rows
          exist, at least one verified, no target" — so the second sentence
          covers both. The wire's `why` field is the future fix if that
          distinction ever has to be drawn; do not add a wire field now. */}
      {snapshotsLoaded && connected && snapshots.length > 0 && !targetName && (
        <p className="mb-3 text-meta text-muted">
          {snapshots.some((s) => s.verifiedWorking)
            ? "Your setup already matches your last working setup, so there's nothing to go back to right now."
            : "None of these has been seen working yet, so the restore button isn't ready. It appears after Addison next answers you."}
        </p>
      )}

      {/* The outcome of the last save/restore/remove, in plain words. Stays put
          rather than fading — this is a sentence someone re-reads. */}
      {notice && <p className="mb-3 text-fine leading-relaxed text-ink-soft">{notice}</p>}

      {!snapshotsLoaded ? (
        <p className="text-meta text-muted">Looking for your restore points…</p>
      ) : snapshots.length === 0 ? (
        <p className="text-meta text-muted">
          {connected
            ? "None yet. Addison saves the first one as soon as it has something to remember."
            : "Your restore points appear here once Addison's engine is connected."}
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {snapshots.map((snap) => (
            <li key={snap.id} className="rounded border border-line bg-paper px-[14px] py-2.5">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  {/* G4: a permanent row says so, blockily. */}
                  {snap.undeletable && <PermanentTag />}
                  <p className="text-action font-semibold text-ink">{snap.reasonLabel}</p>
                  {/* Same AA reasoning as the destination line above. This is the
                      field a person reads to choose WHICH restore point to click —
                      "Working setup, yesterday 14:02" — so it is the last place in
                      the app that should be set at 2.5:1. */}
                  <p className="mt-0.5 font-mono text-label text-muted">
                    {formatWhen(snap.createdAt)}
                  </p>
                </div>
                {/* A permanent row has NO Remove control — the core refuses to
                    delete it, and offering a button that can only fail would make
                    the guarantee look like a bug. What it gets instead is its own
                    "Restore this one": these are the anchors (the G4 rows and
                    genesis), the points most worth being able to return to by name
                    (contract D7). Fern-filled and rounded, like the one-action
                    restore — a recovery, never the danger token.

                    An ordinary row keeps only Remove. `text-muted` rather than
                    `text-faint` for the same AA reason as the timestamps: quiet is
                    right for the only control that deletes something, unreadable at
                    2.5:1 is not. */}
                {snap.undeletable
                  ? restoreConfirmId !== snap.id && (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => setRestoreConfirmId(snap.id)}
                        className="shrink-0 rounded-sm bg-fern px-3 py-1.5 text-xs font-semibold text-on-accent hover:bg-fern-deep disabled:opacity-50 max-md:min-h-[44px]"
                      >
                        Restore this one
                      </button>
                    )
                  : (
                      <button
                        type="button"
                        onClick={() => void handleDeleteSnapshot(snap.id)}
                        className="shrink-0 text-xs font-medium text-muted hover:text-danger"
                      >
                        Remove
                      </button>
                    )}
              </div>
              {/* The per-row confirm — names the row before the click (never a
                  blind recovery), then the same consequence copy the one-action
                  restore shows. Two-step and inline, never window.confirm(). */}
              {snap.undeletable && restoreConfirmId === snap.id && (
                <div className="mt-2.5 rounded-card bg-fern-tint px-[15px] py-[13px]">
                  <p className="text-meta text-ink-soft">
                    Going back to <span className="font-semibold text-ink">{snap.reasonLabel}</span>
                    <span className="ml-1.5 font-mono text-label text-muted">
                      {formatWhen(snap.createdAt)}
                    </span>
                  </p>
                  <p className="mt-2 text-fine leading-relaxed text-ink-soft">
                    {CONSEQUENCE}
                    {snap.reasonLabel === GENESIS_LABEL && ` ${GENESIS_CONSEQUENCE}`}
                  </p>
                  <div className="mt-2.5 flex flex-wrap items-center gap-3">
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => {
                        setRestoreConfirmId(null);
                        void handleRestoreSnapshot(snap.id);
                      }}
                      className="rounded-pill bg-fern px-[18px] py-[7px] text-xs font-semibold text-on-accent hover:bg-fern-deep disabled:opacity-50"
                    >
                      Restore
                    </button>
                    <button
                      type="button"
                      onClick={() => setRestoreConfirmId(null)}
                      className="text-xs font-medium text-ink-soft hover:text-muted"
                    >
                      Not now
                    </button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// The blocky "PERMANENT" annotation (design-brief-fern shape rule: blocky = a
// live annotation Addison is showing you). Square edges, 2px left fern rule,
// small caps — this row was saved when a safety setting was turned off, so it
// stays for good. Matches WidgetRail's DevTag exactly.
function PermanentTag() {
  return (
    <span className="mb-1 inline-block border-l-2 border-fern pl-1.5 text-tag font-semibold uppercase tracking-caps-wide text-fern-deep">
      Permanent
    </span>
  );
}
