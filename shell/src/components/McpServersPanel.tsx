// Tool servers — the Settings face of the MCP client (Phase-2 step 7, phases 1–2
// of five), in the dark direction's row idiom. It is shown ONLY on the Developer
// and Custom surfaces (keyed off the active profile, never the policy mode);
// Simple never sees it, and the core independently refuses `mcp.add` and
// `mcp.refresh` outside Developer.
//
// THE HONEST LINE IS STILL THE FEATURE HERE, and phase 2 changed exactly half of
// it. Addison can now look at a saved server and list what it offers; it still
// cannot use any of it. So the standing line says both halves in one breath, and
// a row never shows anything the app has not been told: a server nobody has
// checked says so, a check that failed shows the core's own plain sentence, and a
// tool count appears only after a check that actually landed. Inventing a status
// on the one page a person opens to see what Addison can reach is the lie
// Surface.tsx's standing rule 1 forbids — a stale one would be the same lie with
// a timestamp.
//
// Checking is ALWAYS a press. Nothing here checks on mount, on a timer, or after
// an add: Addison makes no network request the person did not just cause.
//
// Removing takes something away, so it is a two-press confirm on the row itself
// (the SkillsSection idiom) rather than the full inline consequence block — there
// is no consequence to explain beyond "it's gone", and the address is in the row.

import { useState } from "react";
import type { McpServersCardState } from "../hooks/useMcpServers";
import type { McpServer } from "../types/ui";
import { RowAction, SurfaceRow } from "./Surface";

// --- Frozen plain-language copy ---------------------------------------------

/** The panel's standing line. Says what a saved server does today and what it
 * still does not, because the alternative is a person believing Addison has
 * gained an ability it has not. Do NOT soften the second half into "Addison will
 * use these": it can see them, and that is all. */
const STANDING_LINE =
  "A tool server is a program on the web that offers Addison extra tools. Addison can " +
  "check what a server offers and list it here — it still can't use any of those tools yet.";

/** Under the address field. Names the one case where a plain http:// address is
 * accepted, so a refusal is never a surprise. */
const ADDRESS_HINT =
  "Its web address, starting with https:// — or http:// if the server runs on this computer.";

const ADD_ACTION = "add a server";
const CHECK_ACTION = "Check now";
const CHECKING_ACTION = "Checking…";

/** What a row says about itself, under its address. One sentence, and never a
 * word Addison has not earned: "not checked yet" is the honest state for a new
 * server AND after a restart, because what a check found is remembered only for
 * as long as the app is running. */
function statusLine(server: McpServer, isChecking: boolean): string {
  if (isChecking) return "Checking what this server offers…";
  switch (server.status) {
    case "ok": {
      const count = server.toolCount ?? server.tools.length;
      const found =
        count === 0
          ? "No tools offered"
          : count === 1
            ? "1 tool found"
            : `${count} tools found`;
      const when = formatWhen(server.checkedAt);
      const skipped =
        server.skipped && server.skipped > 0
          ? ` ${server.skipped} more Addison won't take.`
          : "";
      return `${found}${when ? `, checked ${when}` : ""}. Addison can't use them yet.${skipped}`;
    }
    case "failed":
      return server.error ?? "That check didn't work. Try again in a moment.";
    default:
      return "Not checked yet.";
  }
}

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
  const {
    servers,
    serversLoaded,
    busy,
    checking,
    error,
    notice,
    handleAdd,
    handleRemove,
    handleCheck,
  } = state;

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
        servers.map((server) => {
          const isChecking = checking.has(server.id);
          return (
            <SurfaceRow
              key={server.id}
              name={server.name}
              value={server.addedAt ? `added ${formatWhen(server.addedAt)}` : undefined}
              // Two controls, so the row composes them itself: checking is the
              // ordinary action and removing is the destructive one, and a single
              // action slot would have made a person choose which of the two the
              // row is for.
              actions={
                <>
                  <RowAction
                    onClick={() => void handleCheck(server.id)}
                    disabled={isChecking || busy}
                    ariaLabel={`Check ${server.name} now`}
                  >
                    {isChecking ? CHECKING_ACTION : CHECK_ACTION}
                  </RowAction>
                  <RowAction
                    tone="danger"
                    onClick={() => remove(server)}
                    disabled={busy}
                    ariaLabel={`Remove ${server.name}`}
                  >
                    {confirmingRemove === server.id ? "Really remove?" : "Remove"}
                  </RowAction>
                </>
              }
            >
              <p className="m-0 mt-1 break-all font-mono text-[11px] text-muted">{server.url}</p>
              <p className="m-0 mt-1 text-[12px] leading-[1.55] text-muted">
                {statusLine(server, isChecking)}
              </p>
            </SurfaceRow>
          );
        })
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
