// The coding-harness workspace-trust boundary (workspace.list / grantTrust /
// revokeTrust / pickDirectory; Phase-2 step 5). This hook owns the currently-
// trusted roots, the grant/revoke handlers, the folder picker, and the two
// transient lines the card shows — a plain error (a refused grant, in the core's
// own words) and a plain notice (a revoke landed). It mirrors useGuards /
// useSnapshots.
//
// One thing it does NOT own, on purpose: the trust decision itself. WHETHER to
// grant is the person's, gated by the card's two-step confirm; the core is the
// one that floor-checks the folder and refuses Addison's own data dir. The confirm
// is honesty about what trusting a folder costs (Addison stops asking before each
// file change there), not a gate the core relies on.
//
// The card is rendered only on the Developer/Custom surfaces (keyed off the active
// profile, never the mode) — that gate lives in SettingsPage, not here.

import { useCallback, useEffect, useState } from "react";
import type { WorkspaceRoot } from "../types/ui";
import { ipc, isEngineConnected, subscribeCoreState } from "../ipc/client";

interface UseWorkspaceArgs {
  connected: boolean;
}

export function useWorkspace({ connected }: UseWorkspaceArgs) {
  const [roots, setRoots] = useState<WorkspaceRoot[]>([]);
  // "not loaded yet" vs "loaded" — the card shows a quiet line before its list so
  // a slow first fetch never renders an empty, ambiguous "no trusted folders".
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  // The last refused grant, in the core's own already-plain words (e.g. the
  // data-dir refusal). Cleared when the next grant/revoke starts.
  const [error, setError] = useState<string | null>(null);
  // The last revoke's plain outcome line ("Addison will ask first again in …").
  // Stays put rather than fading — a sentence someone re-reads. Cleared on the
  // next action.
  const [notice, setNotice] = useState<string | null>(null);

  const refreshWorkspace = useCallback(() => {
    if (!isEngineConnected()) return;
    ipc
      .listWorkspaceRoots()
      .then((r) => {
        setRoots(r);
        setLoaded(true);
      })
      .catch(() => {
        // Keep the last-known list rather than blanking the card; still stop the
        // looking-for line.
        setLoaded(true);
      });
  }, []);

  useEffect(() => {
    refreshWorkspace();
    // Every "ready" is a fresh engine — re-read, like the other data hooks.
    return subscribeCoreState((state) => {
      if (state === "ready") refreshWorkspace();
    });
  }, [connected, refreshWorkspace]);

  /** Open the OS folder picker. Resolves to the chosen absolute path, or `null`
   * when the person cancelled or no picker is available (the card does nothing
   * then). Never throws — a picker failure is just "no folder chosen". */
  const pickDirectory = useCallback(async (): Promise<string | null> => {
    try {
      return await ipc.pickWorkspaceDirectory();
    } catch {
      return null;
    }
  }, []);

  /** Grant trust to a folder. A refusal (the folder is Addison's own data dir, or
   * doesn't exist) is a resolved {ok:false} carrying the core's plain sentence,
   * which we surface as one calm line — never a stack trace. Returns whether it
   * landed so the card can close its confirm on success. */
  const handleGrant = useCallback(
    async (directory: string): Promise<boolean> => {
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        const res = await ipc.grantWorkspaceTrust(directory);
        if (res.ok) {
          return true;
        }
        setError(res.error ?? "Addison couldn't trust that folder just now.");
        return false;
      } catch {
        setError("Addison couldn't trust that folder just now.");
        return false;
      } finally {
        setBusy(false);
        refreshWorkspace();
      }
    },
    [refreshWorkspace],
  );

  /** Stop trusting a folder. Revoking makes Addison ask first again, so it is a
   * tightening — no confirm, straight through. On success the card shows the
   * frozen "Addison will ask first again in …" line. */
  const handleRevoke = useCallback(
    async (directory: string): Promise<void> => {
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        const res = await ipc.revokeWorkspaceTrust(directory);
        if (res.ok) {
          setNotice(`Addison will ask first again in ${directory}.`);
        } else {
          setError(res.error ?? "Addison couldn't stop trusting that folder just now.");
        }
      } catch {
        setError("Addison couldn't stop trusting that folder just now.");
      } finally {
        setBusy(false);
        refreshWorkspace();
      }
    },
    [refreshWorkspace],
  );

  return {
    roots,
    rootsLoaded: loaded,
    busy,
    error,
    notice,
    refreshWorkspace,
    pickDirectory,
    handleGrant,
    handleRevoke,
  };
}

export type WorkspaceCardState = ReturnType<typeof useWorkspace>;
