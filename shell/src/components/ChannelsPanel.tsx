// Your phone — the Settings face of the messaging channels (phases 1-2 of three;
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
// WHAT ADDISON WILL AND WILL NOT DO FROM A PHONE is a standing list rather than a
// link (§3.12 item 4), and in this phase it says the honest thing: Addison answers
// in words and uses no tools at all. When phase 3 gives the remote floor its three
// ids, THIS LIST is what changes with it — the person's vocabulary, not the code's.
//
// TWO DIFFERENT QUESTIONS, KEPT APART, and this panel is where a person would
// otherwise be lied to about them. `Channel.enabled` is what they last chose and is
// saved; the STATUS is whether Addison is listening right now. Nothing starts
// listening when the app opens, so after a restart a switched-on connection is not
// listening — and every control here reads the live state, so the switch never
// points the wrong way.
//
// G1: the token field's value goes straight to the Rust `store_channel_key` command
// and nowhere else. It is not held in this component beyond the keystroke, not sent
// to the core, and never read back — there is no route in the window that reads a
// stored token.
//
// Removing takes something away, so it is a two-press confirm on the row itself (the
// SkillsSection / McpServersPanel idiom) rather than a browser confirm(). Revoking a
// paired phone is the same shape, for the same reason.

import { useState } from "react";
import type { ChannelsCardState } from "../hooks/useChannels";
import type { Channel, ChannelStatus } from "../types/ui";
import { RowAction, SurfaceRow } from "./Surface";

// --- Frozen plain-language copy ---------------------------------------------

/** THE PRIVACY SENTENCE. Verbatim from the plan's §3.12; two sentences that say
 * exactly what leaves this computer and what does not. It is one of the few strings
 * in the app that must not be improved into something vaguer. */
export const PRIVACY_LINE =
  "Messages you send from your phone travel through Telegram's servers, the way any " +
  "other Telegram message does. Everything else stays on this computer.";

/** THE STANDING LIST (§3.12 item 4) — the remote floor in the person's own
 * vocabulary. In this phase the floor is EMPTY, so the honest version of this list
 * is one sentence about words and one about everything else. Phase 3 is the commit
 * that adds "look things up on the web" and "do the maths" to the first line. */
export const WHAT_IT_WILL_DO =
  "From your phone, Addison answers in words. It can't change a file, run anything, " +
  "or touch your computer from a message — that all waits until you're back.";

/** Under the token field. Says where the token goes, in the words the API-keys
 * section already uses for the same journey. */
const TOKEN_HINT =
  "The token goes straight to your computer's keychain and is never shown again — not even here.";

/** Shown ONLY when more than one connection of this transport is saved, because
 * that is when it becomes true and load-bearing: the keychain account is
 * `channel-key:<kind>`, namespaced by TRANSPORT rather than by row (the plan's
 * §3.9), so every Telegram connection on this computer shares ONE saved token. */
const SHARED_TOKEN_NOTE = (label: string) =>
  `All ${label} connections on this computer share one saved token, so this replaces it ` +
  `for the others too.`;

/** Beside the pairing code. One sentence about what pairing MEANS — not about how
 * it works, and never the word "nonce". */
const PAIRING_EXPLAINER =
  "Send this code to your bot from the phone you want to use. Only that phone will " +
  "be able to message Addison, and you can undo it here at any time.";

const ADD_ACTION = "add a connection";
const CHECK_ACTION = "Check now";
const CHECKING_ACTION = "Checking…";

/** The only transport with an adapter. Fixed rather than chosen: a picker with one
 * item is a question with one answer, and the CHECK in the core's schema refuses
 * anything else anyway. */
const KIND = "telegram" as const;
const KIND_LABEL = "Telegram";

/** What a row says about its token, in plain words. "unknown" is deliberately NOT
 * rendered as "no token saved": Addison genuinely does not know until it has asked,
 * and saying it does would be the one lie a page about what is stored on your
 * computer must not tell. */
function tokenLine(channel: Channel): string {
  switch (channel.tokenPresent) {
    case "present":
      return "A token is saved and Addison has checked it.";
    case "absent":
      return "No token saved yet.";
    default:
      return "Addison hasn't checked whether a token is saved.";
  }
}

/** What a connection is doing, in plain words. THE DIFFERENCE BETWEEN QUIET AND
 * BROKEN is the whole reason this line exists: a phone that goes silent looks
 * exactly like a phone nobody has messaged, and the desk is the only place that can
 * tell somebody which it is. */
export function statusLine(status: ChannelStatus | undefined): string {
  if (!status) return "Not listening.";
  switch (status.state) {
    case "listening":
      return status.backoffSeconds > 0
        ? "Listening again shortly — Telegram isn't answering."
        : "Listening for messages from your phone.";
    case "backing_off":
      return "Telegram isn't answering. Addison is still trying.";
    case "token_rejected":
      return "Telegram refused the saved token, so Addison stopped listening.";
    case "no_token":
      return "No token saved, so there is nothing to listen with.";
    default:
      return "Not listening.";
  }
}

function formatWhen(at?: number): string {
  if (!at) return "";
  try {
    return new Date(at * 1000).toLocaleDateString(undefined, {
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
  const {
    channels,
    channelsLoaded,
    busy,
    checking,
    error,
    notice,
    statuses,
    pairings,
    pairing,
    lastRemoteTurn,
    handleAdd,
    handleRemove,
    handleSaveToken,
    handleConnect,
    handleSetEnabled,
    handleBeginPairing,
    handleCancelPairing,
    handleRevokePairing,
  } = state;

  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  // The token being typed, per row. Held only until it is handed to the shell, and
  // cleared the moment the save lands — the field is a conduit, not a store.
  const [token, setToken] = useState("");
  const [tokenFor, setTokenFor] = useState<string | null>(null);
  const [confirmingRemove, setConfirmingRemove] = useState<string | null>(null);
  const [confirmingRevoke, setConfirmingRevoke] = useState<string | null>(null);

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

  function revoke(channelId: string, pairingId: string) {
    if (confirmingRevoke !== pairingId) {
      setConfirmingRevoke(pairingId);
      return;
    }
    setConfirmingRevoke(null);
    void handleRevokePairing(channelId, pairingId);
  }

  return (
    <>
      {/* FIRST, always. See the file header. */}
      <SurfaceRow wrap name={PRIVACY_LINE} />
      <SurfaceRow wrap name={WHAT_IT_WILL_DO} />

      {/* A refusal in the core's or the shell's own already-plain words. */}
      {error && <SurfaceRow wrap name={error} />}

      {/* The outcome of the last removal, token save or check. */}
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
        channels.map((channel) => {
          const status = statuses[channel.id];
          const live = status?.state === "listening" || status?.state === "backing_off";
          const paired = pairings[channel.id] ?? [];
          const pairingHere = pairing?.channelId === channel.id ? pairing : null;
          return (
            <SurfaceRow
              key={channel.id}
              name={channel.name}
              value={channel.addedAt ? `added ${formatWhen(channel.addedAt)}` : undefined}
              actions={
                <>
                  <RowAction
                    onClick={() => void handleConnect(channel)}
                    disabled={busy}
                    ariaLabel={`Check ${channel.name}`}
                  >
                    {checking === channel.id ? CHECKING_ACTION : CHECK_ACTION}
                  </RowAction>
                  <RowAction
                    onClick={() => void handleSetEnabled(channel, !live)}
                    disabled={busy}
                    ariaLabel={live ? `Stop listening to ${channel.name}` : `Listen to ${channel.name}`}
                  >
                    {live ? "Stop listening" : "Start listening"}
                  </RowAction>
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
              <p className="m-0 mt-1 font-mono text-[11px] text-muted">
                {KIND_LABEL}
                {status?.connectedAs ? ` · connected as ${status.connectedAs}` : ""}
              </p>
              <p className="m-0 mt-1 text-[12px] leading-[1.55] text-muted">{statusLine(status)}</p>
              <p className="m-0 mt-1 text-[12px] leading-[1.55] text-muted">{tokenLine(channel)}</p>
              {status?.error && (
                <p className="m-0 mt-1 text-[12px] leading-[1.55] text-muted">{status.error}</p>
              )}
              {/* Strangers knocking. A COUNT and never a message: an unpaired phone
                  is ignored in silence, because a reply would tell whoever sent it
                  that the bot is real and somebody is behind it. */}
              {status && status.unknownSenders > 0 && (
                <p className="m-0 mt-1 text-[12px] leading-[1.55] text-muted">
                  {status.unknownSenders === 1
                    ? "1 message came from a phone that isn't paired. Addison didn't reply."
                    : `${status.unknownSenders} messages came from phones that aren't paired. ` +
                      "Addison didn't reply."}
                </p>
              )}
              {lastRemoteTurn?.channelId === channel.id && (
                <p className="m-0 mt-1 text-[12px] leading-[1.55] text-muted">
                  {lastRemoteTurn.phase === "answered"
                    ? "Addison answered a message from your phone."
                    : lastRemoteTurn.phase === "paired"
                      ? "A phone paired with this connection."
                      : lastRemoteTurn.summary
                        ? lastRemoteTurn.summary
                        : "A message came in from your phone."}
                </p>
              )}

              {/* Paired phones, each with a Revoke. The whole control surface a
                  pairing has, and it answers in every profile. */}
              {paired.length > 0 && (
                <div className="mt-2.5 flex flex-col gap-1.5">
                  {paired.map((device) => (
                    <div key={device.id} className="flex items-baseline gap-3">
                      <span className="text-[12px] text-muted">
                        {device.label}
                        {device.pairedAt ? ` · paired ${formatWhen(device.pairedAt)}` : ""}
                      </span>
                      <span className="flex-1" />
                      <RowAction
                        tone="danger"
                        onClick={() => revoke(channel.id, device.id)}
                        disabled={busy}
                        ariaLabel={`Revoke ${device.label}`}
                      >
                        {confirmingRevoke === device.id ? "Really revoke?" : "Revoke"}
                      </RowAction>
                    </div>
                  ))}
                </div>
              )}

              {/* Pairing: the code is shown HERE and typed on the phone. */}
              {pairingHere ? (
                <div className="mt-2.5 flex flex-col gap-2">
                  <p className="m-0 font-mono text-[18px] tracking-[0.2em] text-ink">
                    {pairingHere.code}
                  </p>
                  <p className="m-0 text-[12px] leading-[1.55] text-muted">
                    {PAIRING_EXPLAINER}
                  </p>
                  <div className="flex items-baseline gap-5">
                    <RowAction
                      tone="muted"
                      onClick={() => void handleCancelPairing(channel)}
                      disabled={busy}
                    >
                      Cancel pairing
                    </RowAction>
                  </div>
                </div>
              ) : (
                <div className="mt-2.5 flex items-baseline gap-5">
                  <RowAction
                    onClick={() => void handleBeginPairing(channel)}
                    disabled={busy}
                    ariaLabel={`Pair a phone with ${channel.name}`}
                  >
                    Pair a phone
                  </RowAction>
                </div>
              )}

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
          );
        })
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
