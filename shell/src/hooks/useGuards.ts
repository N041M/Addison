// The two tunable prompting guards of the Custom profile (guards.get /
// guards.set; Phase-2 step 2, contract D2/D5). This hook owns the current guard
// values and the save handler, mirroring useSnapshots / useSkills.
//
// Two things it does NOT own, on purpose:
//   1. The weakening decision. Whether a save moves a guard DOWN its strictness
//      order — and so needs the permanent-anchor confirm first — is a pure UI
//      question the panel answers before it ever calls `handleSave`. The core
//      mints the anchor regardless; the confirm is honesty about what the click
//      will cost, not a gate the core relies on.
//   2. The profile check. These guards are effective only under the Custom
//      profile; the panel is rendered only then. `guards.active` still rides
//      along so a caller can tell.

import { useCallback, useEffect, useState } from "react";
import type { GuardsState, DestructiveCardGuard, AutoGrantScopeGuard } from "../types/ui";
import { ipc, isEngineConnected, subscribeCoreState } from "../ipc/client";

interface UseGuardsArgs {
  connected: boolean;
  /**
   * Called after a guards.set actually succeeded. A weakening save mints a new
   * permanent restore point core-side, so the restore-points list must be
   * re-read — the way back the confirm just promised should appear at once.
   */
  onSaved?: () => void;
}

/** The patch a save sends — only the guard(s) that changed. */
export interface GuardPatch {
  destructiveCard?: DestructiveCardGuard;
  autoGrantScope?: AutoGrantScopeGuard;
}

export function useGuards({ connected, onSaved }: UseGuardsArgs) {
  const [guards, setGuards] = useState<GuardsState | null>(null);
  // "not loaded yet" vs "loaded" — the panel shows a quiet line before its
  // controls so a slow first fetch never renders empty radio groups.
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  // The last failed save, in the core's own already-plain words. Cleared when the
  // next save starts.
  const [error, setError] = useState<string | null>(null);

  const refreshGuards = useCallback(() => {
    if (!isEngineConnected()) return;
    ipc
      .getGuards()
      .then((g) => {
        setGuards(g);
        setLoaded(true);
      })
      .catch(() => {
        // Keep the last-known values rather than blanking the panel; still stop
        // the looking-for line.
        setLoaded(true);
      });
  }, []);

  useEffect(() => {
    refreshGuards();
    // Every "ready" is a fresh engine — re-read, like the other data hooks.
    return subscribeCoreState((state) => {
      if (state === "ready") refreshGuards();
    });
  }, [connected, refreshGuards]);

  async function handleSave(patch: GuardPatch) {
    setBusy(true);
    setError(null);
    try {
      const res = await ipc.setGuards(patch);
      if (res.ok) {
        onSaved?.();
      } else {
        // Already user-ready from the core (a bad value, or the anchor couldn't
        // be saved so nothing changed). Never a stack trace.
        setError(res.error ?? "Addison couldn't change that setting just now.");
      }
    } catch {
      setError("Addison couldn't change that setting just now.");
    } finally {
      setBusy(false);
      refreshGuards();
    }
  }

  return {
    guards,
    guardsLoaded: loaded,
    busy,
    error,
    refreshGuards,
    handleSave,
  };
}

export type GuardsCardState = ReturnType<typeof useGuards>;
