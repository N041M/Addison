// Restore points — the frontend half of GLOBAL FLOOR G3 (guaranteed rollback;
// amendment §3, contract §7). A restore point is a copy of Addison's settings,
// services, notes, widgets and routines. It never holds a saved key (those stay
// in the system keychain) and never holds a chat.
//
// This hook owns the list plus the save / restore / remove handlers, mirroring
// useSkills. Two things differ, both on purpose:
//
//   1. It refreshes itself on every `subscribeCoreState("ready")` as well as on
//      mount. Every "ready" is a fresh engine — and the one moment a person
//      most wants this card to be truthful is right after Addison came back from
//      something going wrong.
//   2. A restore's outcome does NOT go to the transient status banner. That
//      banner fades after eight seconds; "your setup was put back, and here is
//      what changed" is a sentence someone should be able to re-read. It stays
//      in the card until the next action.

import { useCallback, useEffect, useState } from "react";
import type { Snapshot } from "../types/ui";
import { ipc, isEngineConnected, subscribeCoreState } from "../ipc/client";

interface UseSnapshotsArgs {
  /** Kept for parity with the other data hooks; the refresher gates on the live
   * `isEngineConnected()` check rather than this snapshot. */
  connected: boolean;
  /**
   * Called after a restore actually landed. The restore replaced the profile,
   * the services and the saved items wholesale, so everything the rest of the
   * app cached from before it is now describing a configuration that is gone.
   */
  onRestored?: () => void;
}

export function useSnapshots({ connected, onRestored }: UseSnapshotsArgs) {
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  // Distinguishes "not loaded yet" from "loaded, and genuinely empty", so the
  // card shows a looking-for line before it shows an empty state.
  const [loaded, setLoaded] = useState(false);
  const [lastWorkingId, setLastWorkingId] = useState<string | undefined>();
  const [lastWorkingLabel, setLastWorkingLabel] = useState<string | undefined>();
  const [lastWorkingProfileChange, setLastWorkingProfileChange] = useState<string | undefined>();
  // The core's sticky warning: an automatic restore point couldn't be saved. It
  // stays up until the person saves one themselves — a degraded floor that
  // clears itself is a degraded floor nobody sees.
  const [warning, setWarning] = useState<string | undefined>();
  // The last save/restore/remove outcome, in the card's own words.
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refreshSnapshots = useCallback(() => {
    if (!isEngineConnected()) return;
    ipc
      .listSnapshots()
      .then((list) => {
        setSnapshots(list.snapshots);
        setLastWorkingId(list.lastWorkingId);
        setLastWorkingLabel(list.lastWorkingLabel);
        setLastWorkingProfileChange(list.lastWorkingProfileChange);
        setWarning(list.warning);
        setLoaded(true);
      })
      .catch(() => {
        // Keep the last-known rows rather than blanking the one card a person
        // opens when things have gone wrong; still stop the looking-for line.
        setLoaded(true);
      });
  }, []);

  useEffect(() => {
    refreshSnapshots();
    return subscribeCoreState((state) => {
      if (state === "ready") refreshSnapshots();
    });
  }, [connected, refreshSnapshots]);

  async function handleCreateSnapshot() {
    setBusy(true);
    setNotice(null);
    try {
      const res = await ipc.createSnapshot();
      setNotice(
        res.ok
          ? "Saved. You can come back to this setup later."
          : res.error ?? "Addison couldn't save a restore point just now.",
      );
    } catch {
      setNotice("Addison couldn't save a restore point just now.");
    } finally {
      setBusy(false);
      refreshSnapshots();
    }
  }

  async function handleRestoreLastWorking() {
    setBusy(true);
    setNotice(null);
    try {
      const res = await ipc.restoreLastWorking();
      if (res.ok) {
        // `detail` already carries the profile-change and missing-key sentences
        // the core appended; `binaryMismatch` says the point was saved on a
        // different version of Addison. Both are plain sentences from the core.
        setNotice([res.detail, res.binaryMismatch].filter(Boolean).join(" ") || "Your setup is back.");
        onRestored?.();
      } else {
        setNotice(res.error ?? "There's no saved working setup to go back to yet.");
      }
    } catch {
      // Never a stack trace, and never silence — a floor that fails quietly is
      // worse than one that fails out loud.
      setNotice("Addison couldn't put your setup back just now. Please try again.");
    } finally {
      setBusy(false);
      refreshSnapshots();
    }
  }

  async function handleDeleteSnapshot(id: string) {
    setNotice(null);
    try {
      const res = await ipc.deleteSnapshot(id);
      if (!res.ok) setNotice(res.error ?? "Addison couldn't remove that restore point.");
    } catch {
      setNotice("Addison couldn't remove that restore point.");
    } finally {
      refreshSnapshots();
    }
  }

  return {
    snapshots,
    snapshotsLoaded: loaded,
    lastWorkingId,
    lastWorkingLabel,
    lastWorkingProfileChange,
    warning,
    notice,
    busy,
    refreshSnapshots,
    handleCreateSnapshot,
    handleRestoreLastWorking,
    handleDeleteSnapshot,
  };
}

export type SnapshotsState = ReturnType<typeof useSnapshots>;
