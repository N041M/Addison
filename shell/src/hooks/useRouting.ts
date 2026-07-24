// Routing — how Addison picks which model answers (routing.get / routing.set;
// Phase-2 step 3, contract D7/D8). This hook owns the current routing state and
// the two save handlers, mirroring useGuards / useSnapshots.
//
// It deliberately owns NO policy: whether the person sees the Simple two-option
// toggle or the full picker + chain builder is the core's decision (`surface`),
// read straight off routing.get. The card just renders whichever surface it is
// handed — the frontend never infers the surface from the profile.

import { useCallback, useEffect, useState } from "react";
import type { RoutingState, RoutingStrategy } from "../types/ui";
import { ipc, isEngineConnected, subscribeCoreState } from "../ipc/client";

interface UseRoutingArgs {
  connected: boolean;
  /**
   * Called after a routing.set actually succeeded. Both a strategy change and a
   * custom-chain overwrite snapshot core-side (the hook split in contract D1), so
   * the restore-points list should be re-read — a new way back may have appeared.
   */
  onSaved?: () => void;
}

export function useRouting({ connected, onSaved }: UseRoutingArgs) {
  const [routing, setRouting] = useState<RoutingState | null>(null);
  // "not loaded yet" vs "loaded" — the card shows a quiet line before its
  // controls so a slow first fetch never renders empty option groups.
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  // The last failed save, in the core's own already-plain words (a bad value, or
  // a custom-chain overwrite whose snapshot couldn't be saved so nothing changed).
  // Cleared when the next save starts.
  const [error, setError] = useState<string | null>(null);

  const refreshRouting = useCallback(() => {
    if (!isEngineConnected()) return;
    ipc
      .getRouting()
      .then((r) => {
        setRouting(r);
        setLoaded(true);
      })
      .catch(() => {
        // Keep the last-known values rather than blanking the card; still stop the
        // looking-for line.
        setLoaded(true);
      });
  }, []);

  useEffect(() => {
    refreshRouting();
    // Every "ready" is a fresh engine — re-read, like the other data hooks.
    return subscribeCoreState((state) => {
      if (state === "ready") refreshRouting();
    });
  }, [connected, refreshRouting]);

  // Save a plain strategy change (Quality first / Cost first / Local only / the
  // Custom-order strategy itself). The custom CHAIN is saved by handleSaveChain.
  async function handleSetStrategy(strategy: RoutingStrategy) {
    setBusy(true);
    setError(null);
    try {
      const res = await ipc.setRouting({ strategy });
      if (res.ok) {
        onSaved?.();
      } else {
        setError(res.error ?? "Addison couldn't change how models are picked just now.");
      }
    } catch {
      setError("Addison couldn't change how models are picked just now.");
    } finally {
      setBusy(false);
      refreshRouting();
    }
  }

  // Save an edited custom chain (the full ordered list of model ids). The core
  // REFUSES this if its snapshot can't be saved (user-authored order that exists
  // nowhere else, contract D1) — that refusal arrives as {ok:false} + a plain
  // sentence and the old chain stays intact.
  async function handleSaveChain(chain: string[]) {
    setBusy(true);
    setError(null);
    try {
      const res = await ipc.setRouting({ customChain: chain });
      if (res.ok) {
        onSaved?.();
      } else {
        setError(res.error ?? "Addison couldn't save that model order just now.");
      }
    } catch {
      setError("Addison couldn't save that model order just now.");
    } finally {
      setBusy(false);
      refreshRouting();
    }
  }

  return {
    routing,
    routingLoaded: loaded,
    busy,
    error,
    refreshRouting,
    handleSetStrategy,
    handleSaveChain,
  };
}

export type RoutingCardState = ReturnType<typeof useRouting>;
