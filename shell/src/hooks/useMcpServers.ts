// The MCP tool-server list (mcp.list / mcp.add / mcp.remove / mcp.refresh;
// Phase-2 step 7, phases 1–2 of five). This hook owns the configured servers,
// their discovery state, the add/remove/check handlers, and the two transient
// lines the panel shows — a plain error (the core's own refusal sentence) and a
// plain notice (a removal landed). It mirrors useWorkspace.
//
// EVERY STATUS HERE CAME FROM A CHECK SOMEBODY ASKED FOR. Nothing in this hook
// polls, retries on a timer, or checks a server on mount: discovery is on demand
// only, so a status is never older or newer than the last press of "Check now".
// It is also never invented — a status the core did not send reads as "never
// checked", because a fabricated one on a page about what Addison can reach is the
// one lie that surface must never tell (Surface.tsx's standing rule 1).
//
// `checking` is the ONE state this side owns: the core answers `list` and
// `refresh` on the same worker thread, so it cannot report a check as in flight —
// the row that is in flight is the one we are waiting on, and we know which.
//
// A discovered tool is still not callable. Nothing here should ever say otherwise.
//
// The panel renders only on the Developer/Custom surfaces (keyed off the active
// profile, never the mode) — that gate lives in SettingsPage, not here, and the
// core independently refuses `mcp.add` and `mcp.refresh` outside Developer.

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
  // Which rows have a check in flight. A SET rather than a single id: two servers
  // can be checked one after the other without the first one's row silently
  // reverting to its old status while the second is still out.
  const [checking, setChecking] = useState<ReadonlySet<string>>(() => new Set());

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

  /** Check one server now — the only thing in the app that reaches an MCP server.
   *
   * Never blocks the other rows: `busy` (which disables add and remove) is
   * deliberately not set, because a check can take up to the core's ten-second
   * budget and freezing the whole panel behind a stranger's server would make one
   * slow server look like a broken page. The row's own control is disabled by its
   * membership of `checking` instead.
   *
   * The refreshed row is applied straight from the answer rather than re-fetching
   * the list: the core just told us the newest truth about exactly this row, and a
   * second round-trip could only overwrite it with something older. */
  const handleCheck = useCallback(async (id: string): Promise<void> => {
    setChecking((current) => new Set(current).add(id));
    setError(null);
    setNotice(null);
    try {
      const res = await ipc.refreshMcpServer(id);
      if (res.ok && res.server) {
        const updated = res.server;
        setServers((rows) => rows.map((row) => (row.id === updated.id ? updated : row)));
      } else {
        // The check did not run at all (wrong profile, or the server is gone).
        // A failed CHECK is not this branch — that comes back on the row.
        setError(res.error ?? "Addison couldn't check that server just now.");
      }
    } catch {
      setError("Addison couldn't check that server just now.");
    } finally {
      setChecking((current) => {
        const next = new Set(current);
        next.delete(id);
        return next;
      });
    }
  }, []);

  return {
    servers,
    serversLoaded: loaded,
    busy,
    checking,
    error,
    notice,
    refreshServers,
    handleAdd,
    handleRemove,
    handleCheck,
  };
}

export type McpServersCardState = ReturnType<typeof useMcpServers>;
