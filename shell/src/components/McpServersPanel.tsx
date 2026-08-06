// Tool servers — the Settings face of the MCP client's configuration (Phase-2
// step 7, phase 1 of five), in the dark direction's row idiom. It is shown ONLY on
// the Developer and Custom surfaces (keyed off the active profile, never the policy
// mode); Simple never sees it, and the core independently refuses `mcp.add` outside
// Developer.
//
// THE HONEST LINE IS THE FEATURE HERE. This phase ships configuration and nothing
// else: no connection is made, no tools are discovered, and Addison cannot call
// anything on a saved server. So the standing line says exactly that, and no row
// ever shows a status, a tool count, or a live/offline light — the app has no way
// to know any of that yet, and inventing it on the one page a person checks to see
// what Addison can reach would be the lie Surface.tsx's standing rule 1 forbids.
//
// Removing takes something away, so it is a two-press confirm on the row itself
// (the SkillsSection idiom) rather than the full inline consequence block — there
// is no consequence to explain beyond "it's gone", and the address is in the row.

import { useState } from "react";
import type { McpServersCardState } from "../hooks/useMcpServers";
import { RowAction, SurfaceRow } from "./Surface";

// --- Frozen plain-language copy ---------------------------------------------

/** The panel's standing line. Says what a saved server does today — nothing —
 * because the alternative is a person believing Addison has gained an ability it
 * has not. Do NOT soften this into "Addison will use these": it won't, yet. */
const STANDING_LINE =
  "A tool server is a program on the web that offers Addison extra tools. Saving one " +
  "here stores its address only — Addison doesn't connect to it or use its tools yet.";

/** Under the address field. Names the one case where a plain http:// address is
 * accepted, so a refusal is never a surprise. */
const ADDRESS_HINT =
  "Its web address, starting with https:// — or http:// if the server runs on this computer.";

const ADD_ACTION = "add a server";

function formatWhen(addedAt?: number): string {
  if (!addedAt) return "";
  try {
    return new Date(addedAt * 1000).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });
  } catch {
    return "";
  }
}

export function McpServersPanel({
  connected,
  mcp: state,
}: {
  connected: boolean;
  mcp: McpServersCardState;
}) {
  const { servers, serversLoaded, busy, error, notice, handleAdd, handleRemove } = state;

  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  // Which row is one press away from being removed. Same two-press idiom the
  // skills rows use; never a browser confirm().
  const [confirmingRemove, setConfirmingRemove] = useState<string | null>(null);

  if (!connected) {
    return <SurfaceRow wrap name="These settings appear here once Addison's engine is connected." />;
  }

  function openAdd() {
    setAdding(true);
    setName("");
    setUrl("");
    setConfirmingRemove(null);
  }

  function closeAdd() {
    setAdding(false);
    setName("");
    setUrl("");
  }

  async function save() {
    const trimmedName = name.trim();
    const trimmedUrl = url.trim();
    if (!trimmedName || !trimmedUrl || busy) return;
    const ok = await handleAdd(trimmedName, trimmedUrl);
    // Close only on success; a refusal leaves the form up with the core's plain
    // line above it, so the person can fix the address rather than retype it.
    if (ok) closeAdd();
  }

  function remove(server: { id: string; name: string }) {
    if (confirmingRemove !== server.id) {
      setConfirmingRemove(server.id);
      return;
    }
    setConfirmingRemove(null);
    void handleRemove(server.id, server.name);
  }

  return (
    <>
      <SurfaceRow wrap name={STANDING_LINE} />

      {/* A refusal in the core's own already-plain words — never a stack trace. */}
      {error && <SurfaceRow wrap name={error} />}

      {/* The outcome of the last removal. Stays put rather than fading. */}
      {notice && <SurfaceRow wrap name={notice} />}

      {!serversLoaded ? (
        <SurfaceRow wrap name="Looking for your tool servers…" />
      ) : servers.length === 0 ? (
        !adding && (
          <SurfaceRow
            name="No tool servers yet"
            value="nothing to set up here"
            action={ADD_ACTION}
            actionDisabled={busy}
            onAction={openAdd}
          />
        )
      ) : (
        servers.map((server) => (
          <SurfaceRow
            key={server.id}
            name={server.name}
            value={server.addedAt ? `added ${formatWhen(server.addedAt)}` : undefined}
            action={confirmingRemove === server.id ? "Really remove?" : "Remove"}
            actionAriaLabel={`Remove ${server.name}`}
            actionTone="danger"
            actionDisabled={busy}
            onAction={() => remove(server)}
          >
            <p className="m-0 mt-1 break-all font-mono text-[11px] text-muted">{server.url}</p>
          </SurfaceRow>
        ))
      )}

      {adding ? (
        <SurfaceRow name="New tool server">
          <div className="mt-2.5 flex flex-col gap-2">
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Name this server"
              disabled={busy}
              aria-label="Server name"
              className="w-full border-b border-line bg-transparent py-1.5 font-mono text-[11px] text-ink placeholder:text-disabled focus:border-track-hi disabled:opacity-60"
            />
            <input
              type="text"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://…"
              disabled={busy}
              aria-label="Server address"
              className="w-full border-b border-line bg-transparent py-1.5 font-mono text-[11px] text-ink placeholder:text-disabled focus:border-track-hi disabled:opacity-60"
            />
            <p className="m-0 text-[12px] leading-[1.55] text-muted">{ADDRESS_HINT}</p>
            <div className="flex items-baseline gap-5">
              <RowAction
                onClick={() => void save()}
                disabled={!name.trim() || !url.trim() || busy}
              >
                {busy ? "Saving…" : "Save"}
              </RowAction>
              <RowAction tone="muted" onClick={closeAdd} disabled={busy}>
                Cancel
              </RowAction>
            </div>
          </div>
        </SurfaceRow>
      ) : (
        servers.length > 0 && (
          <SurfaceRow name="Another server" action={ADD_ACTION} actionDisabled={busy} onAction={openAdd} />
        )
      )}
    </>
  );
}
