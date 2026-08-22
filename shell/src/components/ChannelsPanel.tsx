// Your phone — the Settings face of the messaging channels (phase 1 of three;
// docs/messaging-channel-plan.md owns the design). Shown ONLY on the Developer and
// Custom surfaces (keyed off the active profile, never the policy mode); Simple
// never sees it, and the core independently refuses `channel.add` outside Developer.
//
// THE PRIVACY SENTENCE COMES FIRST, before the token field and not after it, and it
// is shown every time this section renders rather than once at setup. Addison is
// local-first and this is the one feature that moves a person's words off the
// machine on purpose; a cost like that belongs on the screen where the choice is
// made, not in a page nobody opens (docs/SAFETY.md's temperament, and the plan's
// §3.12 which fixes the wording).
//
// WHAT PHASE 1 DOES NOT DRAW, deliberately, because the panel must not offer a
// control that does nothing: no enable switch, no "connect" or "check", no pairing
// code and no paired-device list. None of that exists yet — there is no adapter, no
// poll loop and no network call anywhere in this build — and a switch that saves a
// column nothing reads would be the panel telling a person their phone is on.
//
// G1: the token field's value goes straight to the Rust `store_channel_key` command
// and nowhere else. It is not held in this component beyond the keystroke, not sent
// to the core, and never read back — there is no route in the window that reads a
// stored token. What the row can say is `tokenPresent`, which in this phase is
// always "unknown", because proving a token works means asking Telegram.
//
// Removing takes something away, so it is a two-press confirm on the row itself (the
// SkillsSection / McpServersPanel idiom) rather than a browser confirm().

import { useState } from "react";
import type { ChannelsCardState } from "../hooks/useChannels";
import type { Channel } from "../types/ui";
import { RowAction, SurfaceRow } from "./Surface";

// --- Frozen plain-language copy ---------------------------------------------

/** THE PRIVACY SENTENCE. Verbatim from the plan's §3.12; two sentences that say
 * exactly what leaves this computer and what does not. It is one of the few strings
 * in the app that must not be improved into something vaguer. */
export const PRIVACY_LINE =
  "Messages you send from your phone travel through Telegram's servers, the way any " +
  "other Telegram message does. Everything else stays on this computer.";

/** The standing line under it: what a saved connection is today. Honest about the
 * fact that nothing is connected — the MCP panel's rule, and the same reason. A
 * person who saves a token and hears nothing back has not made a mistake. */
const STANDING_LINE =
  "Addison can't talk to your phone yet. Saving a connection here stores its name and " +
  "its token on this computer, ready for when it can.";

/** Under the token field. Says where the token goes, in the words the API-keys
 * section already uses for the same journey. */
const TOKEN_HINT =
  "The token goes straight to your computer's keychain and is never shown again — not even here.";

/** Shown ONLY when more than one connection of this transport is saved, because
 * that is when it becomes true and load-bearing: the keychain account is
 * `channel-key:<kind>`, namespaced by TRANSPORT rather than by row (the plan's
 * §3.9), so every Telegram connection on this computer shares ONE saved token.
 * Somebody with two rows who pastes a token into the second one is replacing the
 * first one's, and a surface that let that happen in silence would be lying about
 * what is stored on their machine. (Removing a row is handled the other way: the
 * token is deleted only when the last connection of its kind goes — see
 * useChannels.) */
const SHARED_TOKEN_NOTE = (label: string) =>
  `All ${label} connections on this computer share one saved token, so this replaces it ` +
  `for the others too.`;

const ADD_ACTION = "add a connection";

/** The only transport with an adapter to come. Fixed rather than chosen: a picker
 * with one item is a question with one answer, and the CHECK in the core's schema
 * refuses anything else anyway. */
const KIND = "telegram" as const;
const KIND_LABEL = "Telegram";

/** What a row says about its token, in plain words. "unknown" is the phase-1 answer
 * for every row and it is deliberately NOT rendered as "no token saved": Addison
 * genuinely does not know, and saying it does would be the one lie a page about what
 * is stored on your computer must not tell. */
function tokenLine(channel: Channel): string {
  switch (channel.tokenPresent) {
    case "present":
      return "A token is saved for this connection.";
    case "absent":
      return "No token saved yet.";
    default:
      return "Addison hasn't checked whether a token is saved.";
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

export function ChannelsPanel({
  connected,
  channels: state,
}: {
  connected: boolean;
  channels: ChannelsCardState;
}) {
  const { channels, channelsLoaded, busy, error, notice, handleAdd, handleRemove, handleSaveToken } =
    state;

  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  // The token being typed, per row. Held only until it is handed to the shell, and
  // cleared the moment the save lands — the field is a conduit, not a store.
  const [token, setToken] = useState("");
  const [tokenFor, setTokenFor] = useState<string | null>(null);
  const [confirmingRemove, setConfirmingRemove] = useState<string | null>(null);

  if (!connected) {
    return (
      <>
        <SurfaceRow wrap name={PRIVACY_LINE} />
        <SurfaceRow wrap name="These settings appear here once Addison's engine is connected." />
      </>
    );
  }

  function openAdd() {
    setAdding(true);
    setName("");
    setConfirmingRemove(null);
  }

  function closeAdd() {
    setAdding(false);
    setName("");
  }

  async function save() {
    const trimmedName = name.trim();
    if (!trimmedName || busy) return;
    const ok = await handleAdd(KIND, trimmedName);
    // Close only on success; a refusal leaves the form up with the core's plain line
    // above it, so the person can fix the name rather than retype it.
    if (ok) closeAdd();
  }

  function openToken(channel: Channel) {
    setTokenFor(channel.id);
    setToken("");
    setConfirmingRemove(null);
  }

  async function saveToken(channel: Channel) {
    const trimmed = token.trim();
    if (!trimmed || busy) return;
    const ok = await handleSaveToken(channel.kind, trimmed);
    // Cleared either way in the success case; on a refusal the field keeps what was
    // typed so a fixable paste (a line break, a stray space) can be corrected in
    // place rather than pasted again from scratch.
    if (ok) {
      setToken("");
      setTokenFor(null);
    }
  }

  function remove(channel: Channel) {
    if (confirmingRemove !== channel.id) {
      setConfirmingRemove(channel.id);
      return;
    }
    setConfirmingRemove(null);
    setTokenFor(null);
    setToken("");
    void handleRemove(channel);
  }

  return (
    <>
      {/* FIRST, always. See the file header. */}
      <SurfaceRow wrap name={PRIVACY_LINE} />
      <SurfaceRow wrap name={STANDING_LINE} />

      {/* A refusal in the core's or the shell's own already-plain words. */}
      {error && <SurfaceRow wrap name={error} />}

      {/* The outcome of the last removal or token save. Stays put rather than fading. */}
      {notice && <SurfaceRow wrap name={notice} />}

      {!channelsLoaded ? (
        <SurfaceRow wrap name="Looking for your phone connections…" />
      ) : channels.length === 0 ? (
        !adding && (
          <SurfaceRow
            name="No phone connections yet"
            value="nothing to set up here"
            action={ADD_ACTION}
            actionDisabled={busy}
            onAction={openAdd}
          />
        )
      ) : (
        channels.map((channel) => (
          <SurfaceRow
            key={channel.id}
            name={channel.name}
            value={channel.addedAt ? `added ${formatWhen(channel.addedAt)}` : undefined}
            actions={
              <>
                <RowAction
                  onClick={() => openToken(channel)}
                  disabled={busy}
                  ariaLabel={`Save a token for ${channel.name}`}
                >
                  {tokenFor === channel.id ? "token…" : "token"}
                </RowAction>
                <RowAction
                  tone="danger"
                  onClick={() => remove(channel)}
                  disabled={busy}
                  ariaLabel={`Remove ${channel.name}`}
                >
                  {confirmingRemove === channel.id ? "Really remove?" : "Remove"}
                </RowAction>
              </>
            }
          >
            <p className="m-0 mt-1 font-mono text-[11px] text-muted">{KIND_LABEL}</p>
            <p className="m-0 mt-1 text-[12px] leading-[1.55] text-muted">{tokenLine(channel)}</p>
            {tokenFor === channel.id && (
              <div className="mt-2.5 flex flex-col gap-2">
                <input
                  type="password"
                  autoComplete="off"
                  spellCheck={false}
                  value={token}
                  onChange={(e) => setToken(e.target.value)}
                  placeholder={`Paste the ${KIND_LABEL} bot token…`}
                  disabled={busy}
                  aria-label={`${channel.name} token`}
                  className="w-full border-b border-line bg-transparent py-1.5 font-mono text-[11px] text-ink placeholder:text-disabled focus:border-track-hi disabled:opacity-60"
                />
                <p className="m-0 text-[12px] leading-[1.55] text-muted">{TOKEN_HINT}</p>
                {channels.filter((other) => other.kind === channel.kind).length > 1 && (
                  <p className="m-0 text-[12px] leading-[1.55] text-muted">
                    {SHARED_TOKEN_NOTE(KIND_LABEL)}
                  </p>
                )}
                <div className="flex items-baseline gap-5">
                  <RowAction onClick={() => void saveToken(channel)} disabled={!token.trim() || busy}>
                    {busy ? "Saving…" : "Save token"}
                  </RowAction>
                  <RowAction
                    tone="muted"
                    onClick={() => {
                      setTokenFor(null);
                      setToken("");
                    }}
                    disabled={busy}
                  >
                    Cancel
                  </RowAction>
                </div>
              </div>
            )}
          </SurfaceRow>
        ))
      )}

      {adding ? (
        <SurfaceRow name="New phone connection">
          <div className="mt-2.5 flex flex-col gap-2">
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Name this connection"
              disabled={busy}
              aria-label="Connection name"
              className="w-full border-b border-line bg-transparent py-1.5 font-mono text-[11px] text-ink placeholder:text-disabled focus:border-track-hi disabled:opacity-60"
            />
            <p className="m-0 text-[12px] leading-[1.55] text-muted">
              Addison connects through {KIND_LABEL}.
            </p>
            <div className="flex items-baseline gap-5">
              <RowAction onClick={() => void save()} disabled={!name.trim() || busy}>
                {busy ? "Saving…" : "Save"}
              </RowAction>
              <RowAction tone="muted" onClick={closeAdd} disabled={busy}>
                Cancel
              </RowAction>
            </div>
          </div>
        </SurfaceRow>
      ) : (
        channels.length > 0 && (
          <SurfaceRow
            name="Another connection"
            action={ADD_ACTION}
            actionDisabled={busy}
            onAction={openAdd}
          />
        )
      )}
    </>
  );
}
