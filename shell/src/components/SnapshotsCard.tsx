// Restore points — the frontend face of GLOBAL FLOOR G3 (guaranteed rollback).
//
// The story this file exists for (amendment §1): someone asked their assistant to
// make things cheaper, it broke their setup, and the rewind didn't work. So the
// copy here is written for the moment AFTER something has gone wrong, for a
// reader who is 54 or 68 and is not enjoying themselves. Plain words, no jargon,
// and the target of a restore is always named before the click — a recovery
// button you press blind isn't a recovery, it's a second gamble.
//
// Three pieces, all in the dark direction's row idiom:
//
//   * `RestorePointsSection` — the Settings section: the one-action summary row
//     ("Going back to …" + "restore"), the honest no-target sentences, the
//     sticky capture-failure warning, and the way through to the full list.
//   * `SnapshotRows`         — the list itself, shared BY VALUE between the
//     Restore-points modal and the Snapshots surface, so the two can never drift
//     into disagreeing about what a permanent row may do.
//   * `SaveSnapshotAction`   — "save one now" (the modal header's control).
//
// SHAPE RULES THAT ARE SAFETY RULES, not styling:
//   * A restore is NEVER the danger token. Going back to a setup that worked is
//     the opposite of a destructive act; the rose token is for deleting things.
//   * The "Permanent" mark on a G4 anchor is blocky (square, 2px accent rule,
//     small caps) because it is Addison telling you something about the record,
//     and it carries NO Remove control — the core refuses to delete the row, and
//     a button that can only fail makes a guarantee look like a bug.
//   * Every confirm is inline and two-step. A browser confirm() cannot carry the
//     consequence copy, and the consequence copy is the entire point.

import { useState } from "react";
import type { SnapshotsState } from "../hooks/useSnapshots";
import type { Snapshot } from "../types/ui";
import { GENESIS_LABEL } from "../ipc/client";
import { PermanentTag, RowAction, RowConfirm, SurfaceRow } from "./Surface";

/** Frozen consequence copy (contract §11.2, on §11.3 item 12's byte-for-byte
 * list). This sentence lives on THIS side of the wire only, and deliberately so:
 * it is what the card shows BEFORE the click, so it is future tense. The core has
 * its own past-tense counterpart for the sentence shown AFTER a restore lands
 * (`_KEYS_UNTOUCHED` in snapshot_manager.py — "…weren't touched"). Two different
 * sentences for two different moments, so neither side asserts the other's; the
 * only assertion of this one is in snapshots.test.ts. Say what comes back AND
 * what doesn't, so nobody hesitates over whether restoring costs them their
 * conversations. */
export const CONSEQUENCE =
  "Your settings, services, notes, widgets and routines go back to how they were. " +
  "Your chats and your saved keys aren't touched.";

/** Appended when the target is the very first restore point: going back there
 * throws away everything set up since install, which the base sentence's "go
 * back to how they were" does not convey on its own. */
export const GENESIS_CONSEQUENCE =
  "This is Addison as it was first installed, so your services, notes, widgets and routines are cleared.";

export function formatWhen(createdAt: number): string {
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

/** "save one now" — the modal header's control. Saving a restore point is a
 * quiet housekeeping act, and a successful one is what clears the core's sticky
 * capture-failure warning (the hook re-reads the list afterwards). */
export function SaveSnapshotAction({
  connected,
  snapshots: state,
}: {
  connected: boolean;
  snapshots: SnapshotsState;
}) {
  return (
    <RowAction
      mono
      disabled={!connected || state.busy}
      onClick={() => void state.handleCreateSnapshot()}
    >
      save one now
    </RowAction>
  );
}

// ---------------------------------------------------------------------------
// The Settings section
// ---------------------------------------------------------------------------

export function RestorePointsSection({
  connected,
  snapshots: state,
  onOpenAll,
}: {
  connected: boolean;
  snapshots: SnapshotsState;
  /** Opens the full restore-points list (the modal). */
  onOpenAll: () => void;
}) {
  // Held until the person confirms or backs out. Inline, never window.confirm().
  const [confirming, setConfirming] = useState(false);

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
  } = state;

  const target = snapshots.find((s) => s.id === lastWorkingId);
  const targetName = lastWorkingLabel ?? target?.reasonLabel;
  const canRestore = connected && Boolean(targetName);

  // One string, so the sentence a person reads is one sentence — the
  // profile-change line is APPENDED, never substituted, because a restore can
  // move you between profiles and so between how freely Addison may act.
  const consequence = [
    CONSEQUENCE,
    lastWorkingProfileChange,
    targetName === GENESIS_LABEL ? GENESIS_CONSEQUENCE : undefined,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <>
      {/* The core's sticky notice that an automatic restore point couldn't be
          saved. It stays until the person saves one themselves. */}
      {warning && <SurfaceRow name={warning} />}

      {/* Name the target, then offer the action. Never the other way round. */}
      {canRestore && targetName && !confirming && (
        <SurfaceRow
          name={
            <>
              Going back to <span className="text-ink">{targetName}</span>
            </>
          }
          value={target ? formatWhen(target.createdAt) : undefined}
          action="restore"
          actionAriaLabel="Restore to the last working state"
          actionDisabled={busy}
          onAction={() => setConfirming(true)}
        />
      )}
      {canRestore && targetName && confirming && (
        <SurfaceRow
          name={consequence}
          actions={
            <>
              <RowAction
                disabled={busy}
                onClick={() => {
                  setConfirming(false);
                  void handleRestoreLastWorking();
                }}
              >
                Restore
              </RowAction>
              <RowAction tone="muted" onClick={() => setConfirming(false)}>
                Not now
              </RowAction>
            </>
          }
        />
      )}

      {/* Restore points exist, but the one-action restore has no target — G3's
          two honest silences. Without a line here the surface lists restore
          points with no way to use them and no reason, and the reader most in
          need of the floor reads that silence as the floor being broken. So
          Addison names which silence this is.

          Imprecision accepted here: the core's 'unreadable' walk outcome is
          indistinguishable from 'identical' on this wire — both arrive as "rows
          exist, at least one verified, no target" — so the second sentence
          covers both. */}
      {snapshotsLoaded && connected && snapshots.length > 0 && !targetName && (
        <SurfaceRow
          name={
            snapshots.some((s) => s.verifiedWorking)
              ? "Your setup already matches your last working setup, so there's nothing to go back to right now."
              : "None of these has been seen working yet, so the restore button isn't ready. It appears after Addison next answers you."
          }
        />
      )}

      {/* The outcome of the last save/restore/remove, in plain words. Stays put
          rather than fading — this is a sentence someone re-reads. */}
      {notice && <SurfaceRow name={notice} />}

      <SurfaceRow
        name="All restore points"
        value={allPointsValue(connected, snapshotsLoaded, snapshots.length)}
        action="open"
        onAction={onOpenAll}
      />
    </>
  );
}

function allPointsValue(connected: boolean, loaded: boolean, count: number): string {
  if (!connected) return "not connected";
  if (!loaded) return "looking…";
  return count === 0 ? "none yet" : String(count);
}

// ---------------------------------------------------------------------------
// The shared list of rows (modal + Snapshots surface)
// ---------------------------------------------------------------------------

export function SnapshotRows({
  connected,
  snapshots: state,
  rows,
}: {
  connected: boolean;
  snapshots: SnapshotsState;
  /** The rows to render. Callers may pass a recency-grouped subset; the row
   * anatomy and the permanent-row semantics are identical either way. */
  rows: Snapshot[];
}) {
  // The permanent row whose per-row "Restore this one" is mid-confirm, if any.
  const [restoreConfirmId, setRestoreConfirmId] = useState<string | null>(null);
  const { busy, restoredId, handleRestoreSnapshot, handleDeleteSnapshot } = state;

  return (
    <>
      {rows.map((snap) => {
        const restored = restoredId === snap.id;
        const confirmingThis = snap.undeletable && restoreConfirmId === snap.id;
        return (
          <SurfaceRow
            key={snap.id}
            // G4: a permanent row says so, blockily.
            tag={snap.undeletable ? <PermanentTag /> : undefined}
            name={snap.reasonLabel}
            value={formatWhen(snap.createdAt)}
            actions={
              restored ? (
                <span className="shrink-0 font-mono text-[10.5px] text-accent">restored ✓</span>
              ) : snap.undeletable ? (
                // A permanent row has NO Remove control — the core refuses to
                // delete it. What it gets instead is its own "Restore this one":
                // these are the anchors (the G4 rows and genesis), the points
                // most worth being able to return to by name (contract D7).
                // Accent, never the danger token — a recovery, not a loss.
                !confirmingThis && (
                  <RowAction
                    disabled={!connected || busy}
                    ariaLabel={`Restore this one: ${snap.reasonLabel}`}
                    onClick={() => setRestoreConfirmId(snap.id)}
                  >
                    Restore this one
                  </RowAction>
                )
              ) : (
                // An ordinary row keeps only Remove. The one-action way back is
                // the summary row's restoreLastWorking, in Settings.
                <RowAction
                  tone="danger"
                  disabled={!connected}
                  ariaLabel={`Remove ${snap.reasonLabel}`}
                  onClick={() => void handleDeleteSnapshot(snap.id)}
                >
                  Remove
                </RowAction>
              )
            }
          >
            {/* The per-row confirm — names the row before the click (never a
                blind recovery), then the same consequence copy the one-action
                restore shows. Two-step and inline, never window.confirm(). */}
            {confirmingThis && (
              <RowConfirm
                busy={busy}
                confirmLabel="Restore"
                onConfirm={() => {
                  setRestoreConfirmId(null);
                  void handleRestoreSnapshot(snap.id);
                }}
                onCancel={() => setRestoreConfirmId(null)}
              >
                <p className="m-0">
                  Going back to <span className="text-ink">{snap.reasonLabel}</span>
                  <span className="ml-1.5 font-mono text-[10.5px] text-muted">
                    {formatWhen(snap.createdAt)}
                  </span>
                </p>
                <p className="m-0 mt-2">
                  {snap.reasonLabel === GENESIS_LABEL
                    ? `${CONSEQUENCE} ${GENESIS_CONSEQUENCE}`
                    : CONSEQUENCE}
                </p>
              </RowConfirm>
            )}
          </SurfaceRow>
        );
      })}
    </>
  );
}

/** The one-line state of the list, when there are no rows to show. Kept out of
 * `SnapshotRows` so the modal and the surface can place it themselves. */
export function snapshotsEmptyLine(connected: boolean, loaded: boolean): string {
  if (!connected) return "Your restore points appear here once Addison's engine is connected.";
  if (!loaded) return "Looking for your restore points…";
  return "None yet. Addison saves the first one as soon as it has something to remember.";
}
