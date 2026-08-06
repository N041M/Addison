// The MCP tool-server list (mcp.list / mcp.add / mcp.remove; Phase-2 step 7,
// phase 1 of five). This hook owns the configured servers, the add/remove
// handlers, and the two transient lines the panel shows — a plain error (the
// core's own refusal sentence) and a plain notice (a removal landed). It mirrors
// useWorkspace.
//
// WHAT IT DOES NOT DO, and must not start doing here: connect to anything. Phase 1
// ships no MCP client at all — adding a server saves an address and that is the
// whole effect. Nothing in this hook should ever report a server as reachable,
// online, or offering tools, because the app has no way to know any of that yet
// and a fabricated status on a page about what Addison can reach is the one lie
// that surface must never tell (Surface.tsx's standing rule 1).
//
// The panel renders only on the Developer/Custom surfaces (keyed off the active
// profile, never the mode) — that gate lives in SettingsPage, not here, and the
// core independently refuses `mcp.add` outside Developer.

import { useCallback, useEffect, useState } from "react";
import type { McpServer } from "../types/ui";
import { ipc, isEngineConnected, subscribeCoreState } from "../ipc/client";

interface UseMcpServersArgs {
  connected: boolean;
}

export function useMcpServers({ connected }: UseMcpServersArgs) {
  const [servers, setServers] = useState<McpServer[]>([]);
  // "not loaded yet" vs "loaded" — a slow first fetch must not render an empty,
  // ambiguous "no servers yet".
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  // The last refusal, in the core's own already-plain words (a bad address, a name
  // already in use, the Developer-only sentence). Cleared when the next action starts.
  const [error, setError] = useState<string | null>(null);
  // The last removal's plain outcome line. Stays put rather than fading.
  const [notice, setNotice] = useState<string | null>(null);

  const refreshServers = useCallback(() => {
    if (!isEngineConnected()) return;
    ipc
      .listMcpServers()
      .then((rows) => {
        setServers(rows);
        setLoaded(true);
      })
      .catch(() => {
        // Keep the last-known list rather than blanking the panel; still stop the
        // looking-for line.
        setLoaded(true);
      });
  }, []);

  useEffect(() => {
    refreshServers();
    // Every "ready" is a fresh engine — re-read, like the other data hooks.
    return subscribeCoreState((state) => {
      if (state === "ready") refreshServers();
    });
  }, [connected, refreshServers]);

  /** Save a server. A refusal is a resolved {ok:false} carrying the core's plain
   * sentence, which we surface as one calm line — never a stack trace. Returns
   * whether it landed so the panel can close its form on success. */
  const handleAdd = useCallback(
    async (name: string, url: string): Promise<boolean> => {
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        const res = await ipc.addMcpServer(name, url);
        if (res.ok) return true;
        setError(res.error ?? "Addison couldn't save that server just now.");
        return false;
      } catch {
        setError("Addison couldn't save that server just now.");
        return false;
      } finally {
        setBusy(false);
        refreshServers();
      }
    },
    [refreshServers],
  );

  /** Forget a server. Removing only ever takes something away, so it goes straight
   * through — the panel's own "Really remove?" second press is the confirmation. */
  const handleRemove = useCallback(
    async (id: string, name: string): Promise<void> => {
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        const res = await ipc.removeMcpServer(id);
        if (res.ok) {
          setNotice(`Addison has forgotten ${name}.`);
        } else {
          setError(res.error ?? "Addison couldn't remove that server just now.");
        }
      } catch {
        setError("Addison couldn't remove that server just now.");
      } finally {
        setBusy(false);
        refreshServers();
      }
    },
    [refreshServers],
  );

  return {
    servers,
    serversLoaded: loaded,
    busy,
    error,
    notice,
    refreshServers,
    handleAdd,
    handleRemove,
  };
}

export type McpServersCardState = ReturnType<typeof useMcpServers>;
