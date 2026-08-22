// Messaging channels — the phone connections a person has saved (phases 1–3, all
// that ship; docs/messaging-channel-plan.md). This hook owns the list, the add/remove
// handlers, the token save, the live status of each connection, the pairing window,
// the paired-device list, the desk queue, and the two transient lines the panel shows — a plain
// error (the core's own refusal sentence, or the shell's) and a plain notice. It
// mirrors useMcpServers, which is the surface this one is modelled on throughout.
//
// PHASE 2: A CONNECTION CAN NOW BE LIVE. `handleConnect` asks Telegram who the
// saved token belongs to; `handleSetEnabled` starts or stops the listening.
//
// PHASE 3: THE DESK QUEUE. From a phone Addison can look things up and do the maths
// — three read-only tools, a closed list the core owns — and everything else comes
// back as a plain sentence plus a note waiting here. A note is a RECORD: this hook
// can read it and dismiss it, and there is no third verb on either side. "Ask this
// here" belongs to the panel and writes the person's own sentence into the composer;
// the turn then runs live, with the ordinary permission card.
//
// TWO DIFFERENT QUESTIONS, KEPT APART. `Channel.enabled` is what the person last
// chose and is saved; `ChannelStatus.state` is whether Addison is listening RIGHT
// NOW. Nothing starts listening when the app opens, so after a restart those two
// legitimately disagree — and every control here is driven by the LIVE one, because
// a switch that shows "on" over a connection that is not listening is the panel
// telling somebody their phone is connected when it is not.
//
// G1 — THE TOKEN NEVER TOUCHES THIS HOOK'S STATE. `handleSaveToken` takes the string
// straight from the field, hands it to the Rust command, and lets it go. It is never
// put in a `useState`, never sent to the core, never logged, and never read back —
// the window has no route that reads a stored token at all. What comes back from the
// core is `tokenPresent`, which says whether one is believed to exist and never any
// part of one.
//
// The panel renders only on the Developer/Custom surfaces (keyed off the active
// profile, never the mode) — that gate lives in SettingsPage, not here, and the core
// independently refuses `channel.add`, `channel.connect` and switching one ON
// outside Developer. Switching one OFF, removing one and revoking a pairing answer
// in EVERY profile: those are tightenings, and a tightening must never be the thing
// a profile switch traps.

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  Channel,
  ChannelKind,
  ChannelOnWake,
  ChannelPairing,
  ChannelPairingWindow,
  ChannelPendingRequest,
  ChannelStatus,
} from "../types/ui";
import {
  deleteChannelKey,
  ipc,
  isEngineConnected,
  storeChannelKey,
  subscribe,
  subscribeCoreState,
} from "../ipc/client";
import { Method } from "../types/protocol";

interface UseChannelsArgs {
  connected: boolean;
}

/** What the last phone turn did, for the one line the panel shows about it.
 *
 * DELIBERATELY NOT THE MESSAGE AND NOT THE ANSWER. A phone turn's words live in
 * their own conversation; this is a notice that one happened, so the desk can see
 * the connection working without the thread on screen being disturbed. */
export interface RemoteTurnNote {
  channelId: string;
  phase: string;
  summary?: string;
  at: number;
}

export function useChannels({ connected }: UseChannelsArgs) {
  const [channels, setChannels] = useState<Channel[]>([]);
  // "not loaded yet" vs "loaded" — a slow first fetch must not render an empty,
  // ambiguous "no connections yet".
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  // The last refusal, in the core's (or the shell's) own already-plain words.
  // Cleared when the next action starts.
  const [error, setError] = useState<string | null>(null);
  // The last outcome line — a removal, or a token saved. Stays put rather than fading.
  const [notice, setNotice] = useState<string | null>(null);
  // Live state per connection id, and the paired phones per connection id. Both
  // are read from the core rather than remembered: this window is a renderer.
  const [statuses, setStatuses] = useState<Record<string, ChannelStatus>>({});
  const [pairings, setPairings] = useState<Record<string, ChannelPairing[]>>({});
  // The open pairing window, if there is one. AT MOST ONE at a time, which matches
  // the core (a second `beginPairing` replaces the first) and matches the fact that
  // v1 listens to one connection at a time.
  const [pairing, setPairing] = useState<ChannelPairingWindow | null>(null);
  const [lastRemoteTurn, setLastRemoteTurn] = useState<RemoteTurnNote | null>(null);
  // The desk queue (phase 3): what a phone asked for that Addison only does at this
  // computer. Read from the core rather than remembered, like everything else here,
  // and kept in one flat list because the core's own queue is one — bounded by age
  // and by count, and empty after a restart.
  const [pendingRequests, setPendingRequests] = useState<ChannelPendingRequest[]>([]);
  // Which row a check is in flight for, so its own action can say "Checking…"
  // without disabling the rest of the panel.
  const [checking, setChecking] = useState<string | null>(null);

  // The ids currently on screen, for the notification handlers — which are
  // subscribed once and must not re-subscribe on every list change.
  const idsRef = useRef<string[]>([]);
  idsRef.current = channels.map((row) => row.id);

  const refreshStatuses = useCallback((ids: string[]) => {
    if (!isEngineConnected()) return;
    for (const id of ids) {
      ipc
        .channelStatus(id)
        .then((status) => setStatuses((prev) => ({ ...prev, [id]: status })))
        .catch(() => {
          // Keep the last-known line rather than blanking it: a dropped answer is
          // not evidence that a connection stopped.
        });
      ipc
        .listChannelPairings(id)
        .then((rows) => setPairings((prev) => ({ ...prev, [id]: rows })))
        .catch(() => {});
    }
  }, []);

  const refreshRequests = useCallback(() => {
    if (!isEngineConnected()) return;
    ipc
      .channelPendingRequests()
      .then(setPendingRequests)
      .catch(() => {
        // Keep the notes already on screen: a dropped answer is not evidence that
        // somebody's request stopped waiting.
      });
  }, []);

  const refreshChannels = useCallback(() => {
    if (!isEngineConnected()) return;
    ipc
      .listChannels()
      .then((rows) => {
        setChannels(rows);
        setLoaded(true);
        refreshStatuses(rows.map((row) => row.id));
      })
      .catch(() => {
        // Keep the last-known list rather than blanking the panel; still stop the
        // looking-for line.
        setLoaded(true);
      });
  }, [refreshStatuses]);

  useEffect(() => {
    refreshChannels();
    refreshRequests();
    // Every "ready" is a fresh engine — re-read, like the other data hooks. A fresh
    // engine is also listening to nothing, which the status re-read is what shows.
    const stopCoreState = subscribeCoreState((state) => {
      if (state === "ready") {
        refreshChannels();
        // A fresh engine has an EMPTY queue — the notes live in memory on the core
        // and a restart is what clears them (owner decision 7). Re-reading is what
        // makes the panel say so, rather than leaving yesterday's notes on screen
        // with a Dismiss that answers nothing.
        refreshRequests();
      }
    });
    // The core says when a connection's state moves, so the panel re-renders
    // without polling. Nothing here is authoritative: the frame carries the new
    // state, and the status re-read is what fills in the rest.
    const stopState = subscribe(Method.ChannelStateChanged, (params) => {
      const id = typeof params.id === "string" ? params.id : null;
      if (!id) return;
      refreshStatuses([id]);
    });
    // A phone turn started or finished. NOT the streaming or activity channels, on
    // purpose: a phone turn's words must never appear inside the conversation on
    // this screen.
    const stopTurn = subscribe(Method.ChannelRemoteTurn, (params) => {
      const id = typeof params.id === "string" ? params.id : null;
      if (!id) return;
      setLastRemoteTurn({
        channelId: id,
        phase: typeof params.phase === "string" ? params.phase : "",
        summary: typeof params.summary === "string" ? params.summary : undefined,
        at: Date.now(),
      });
      // A turn that paired a phone changes the device list, and a turn of any kind
      // may have changed the unknown-sender count.
      refreshStatuses([id]);
    });
    // A phone asked for something Addison only does at this computer, and it is now
    // waiting on the desk. The frame CARRIES the note, so a panel that is already
    // open shows it without a round trip — and the list is re-read anyway, because
    // the core's queue is the truth and this frame is only ever a prompt to look.
    const stopQueued = subscribe(Method.ChannelRequestQueued, () => {
      refreshRequests();
    });
    return () => {
      stopCoreState();
      stopState();
      stopTurn();
      stopQueued();
    };
  }, [connected, refreshChannels, refreshRequests, refreshStatuses]);

  /** Save a connection. A refusal is a resolved {ok:false} carrying the core's plain
   * sentence, which we surface as one calm line — never a stack trace. Returns
   * whether it landed so the panel can close its form on success. */
  const handleAdd = useCallback(
    async (kind: ChannelKind, name: string): Promise<boolean> => {
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        const res = await ipc.addChannel(kind, name);
        if (res.ok) return true;
        setError(res.error ?? "Addison couldn't save that connection just now.");
        return false;
      } catch {
        setError("Addison couldn't save that connection just now.");
        return false;
      } finally {
        setBusy(false);
        refreshChannels();
      }
    },
    [refreshChannels],
  );

  /** Forget a connection: the token first, then the row.
   *
   * THE ORDER IS THE POINT. The keychain item is deleted from HERE, because the
   * core's side of the keychain is a read and nothing more. Doing it first means the
   * failure mode is a row still listed with its token gone — visible, and removable
   * again by pressing the same button — rather than a token left on the machine
   * belonging to a connection nothing can name any more.
   *
   * A token that could not be deleted therefore STOPS the removal and says so. The
   * person can try again; the row is still there to try it from.
   *
   * **AND THE TOKEN IS NOT ALWAYS THIS ROW'S TO DELETE.** The keychain account is
   * `channel-key:<kind>` — namespaced by TRANSPORT, not by row (the plan's §3.9) —
   * so every Telegram connection on this computer shares one saved token. Removing
   * one of two Telegram rows must therefore NOT delete it: that would take the
   * remaining connection's token away, silently, in the one direction nothing can
   * put back. So the delete happens only when this is the last row of its kind.
   * Rows of a kind are counted from the list this hook already holds, which is the
   * same list the person is looking at. */
  const handleRemove = useCallback(
    async (channel: Channel): Promise<void> => {
      setBusy(true);
      setError(null);
      setNotice(null);
      const sharesKind = channels.some(
        (other) => other.id !== channel.id && other.kind === channel.kind,
      );
      if (!sharesKind) {
        try {
          await deleteChannelKey(channel.kind);
        } catch (err) {
          setError(
            err instanceof Error && err.message
              ? err.message
              : "Addison couldn't remove that token just now.",
          );
          setBusy(false);
          return;
        }
      }
      try {
        const res = await ipc.removeChannel(channel.id);
        if (res.ok) {
          setNotice(`Addison has forgotten ${channel.name}.`);
        } else {
          setError(res.error ?? "Addison couldn't remove that connection just now.");
        }
      } catch {
        setError("Addison couldn't remove that connection just now.");
      } finally {
        setBusy(false);
        refreshChannels();
      }
    },
    [channels, refreshChannels],
  );

  /** Save the bot token for a channel's transport, straight into the OS keychain.
   *
   * The token goes to the Rust command and NOWHERE else: not into this hook's state,
   * not into a core payload, not into the list this hook holds (G1). The list is
   * re-read afterwards, because the row is the only thing that can report on a
   * token — and it will still say "unknown" until somebody presses Check now, which
   * is the only thing that can turn a saved token into a known one.
   *
   * Returns whether it landed, so the panel can clear its field only on success. */
  const handleSaveToken = useCallback(
    async (kind: ChannelKind, token: string): Promise<boolean> => {
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        await storeChannelKey(kind, token);
        setNotice("Token saved to your computer's keychain.");
        return true;
      } catch (err) {
        // The shell's own plain sentence where it has one (a line break in the
        // paste, hidden characters) — those are the ones that tell somebody what to
        // fix, and replacing them with a generic line is the mystifying failure the
        // store boundary exists to remove.
        setError(
          err instanceof Error && err.message
            ? err.message
            : "Addison couldn't save that token just now.",
        );
        return false;
      } finally {
        setBusy(false);
        refreshChannels();
      }
    },
    [refreshChannels],
  );

  /** Check the saved token — ONE request that both validates it and comes back with
   * the bot it belongs to. It starts no listening: checking is a question, and
   * switching on is a decision. */
  const handleConnect = useCallback(
    async (channel: Channel): Promise<void> => {
      setBusy(true);
      setChecking(channel.id);
      setError(null);
      setNotice(null);
      try {
        const res = await ipc.connectChannel(channel.id);
        if (res.ok) {
          setNotice(
            res.connectedAs
              ? `Connected as ${res.connectedAs}.`
              : "That token works.",
          );
        } else {
          setError(res.error ?? "Addison couldn't check that connection just now.");
        }
      } catch {
        setError("Addison couldn't check that connection just now.");
      } finally {
        setChecking(null);
        setBusy(false);
        refreshChannels();
      }
    },
    [refreshChannels],
  );

  /** Start or stop listening. The core refuses a second live connection with its own
   * plain sentence naming the other one (owner decision 11), and refuses switching
   * one on before its token has been checked. */
  const handleSetEnabled = useCallback(
    async (channel: Channel, enabled: boolean): Promise<void> => {
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        const res = await ipc.setChannelEnabled(channel.id, enabled);
        if (!res.ok) {
          setError(res.error ?? "Addison couldn't change that just now.");
        }
      } catch {
        setError("Addison couldn't change that just now.");
      } finally {
        setBusy(false);
        refreshChannels();
      }
    },
    [refreshChannels],
  );

  /** Choose what happens to a message that arrived while the Mac was asleep (owner
   * decision 8). The core refuses "answer" outside Developer in its own words and
   * accepts "decline" in every profile — this hook prints whichever sentence comes
   * back and never writes a second one. */
  const handleSetOnWake = useCallback(
    async (channel: Channel, onWake: ChannelOnWake): Promise<void> => {
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        const res = await ipc.setChannelOnWake(channel.id, onWake);
        if (!res.ok) {
          setError(res.error ?? "Addison couldn't change that just now.");
        }
      } catch {
        setError("Addison couldn't change that just now.");
      } finally {
        setBusy(false);
        refreshChannels();
      }
    },
    [refreshChannels],
  );

  /** Ask for a pairing code. It is shown on THIS screen and typed on the phone —
   * the secret stays on the trusted surface and only the proof goes over the wire. */
  const handleBeginPairing = useCallback(
    async (channel: Channel): Promise<void> => {
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        const res = await ipc.beginChannelPairing(channel.id);
        if (res.ok && res.code) {
          setPairing({
            channelId: channel.id,
            code: res.code,
            expiresAt: res.expiresAt ?? 0,
          });
        } else {
          setError(res.error ?? "Addison couldn't start pairing just now.");
        }
      } catch {
        setError("Addison couldn't start pairing just now.");
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const handleCancelPairing = useCallback(
    async (channel: Channel): Promise<void> => {
      setPairing(null);
      try {
        await ipc.cancelChannelPairing(channel.id);
      } catch {
        // Closing a window that is already closed is not a failure worth a line.
      }
      refreshStatuses([channel.id]);
    },
    [refreshStatuses],
  );

  /** Revoke one paired phone. Answers in every profile — the whole control surface a
   * pairing has, and a tightening is never trapped. */
  const handleRevokePairing = useCallback(
    async (channelId: string, pairingId: string): Promise<void> => {
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        const res = await ipc.revokeChannelPairing(pairingId);
        if (res.ok) {
          setNotice("That phone can't message Addison any more.");
        } else {
          setError(res.error ?? "Addison couldn't remove that phone just now.");
        }
      } catch {
        setError("Addison couldn't remove that phone just now.");
      } finally {
        setBusy(false);
        refreshStatuses([channelId]);
      }
    },
    [refreshStatuses],
  );

  /** Take one note off the desk. THE ONLY THING THIS HOOK DOES TO A REQUEST besides
   * reading it: there is no "run it now" here and none in the core either. The
   * panel's other affordance, "Ask this here", writes the person's own sentence into
   * the composer and returns to chat — they press Send, and the turn runs live with
   * the ordinary permission card. */
  const handleDismissRequest = useCallback(
    async (requestId: string): Promise<void> => {
      // Optimistic, and safe to be: the core is idempotent about a note that is
      // already gone, and the list is re-read either way.
      setPendingRequests((prev) => prev.filter((row) => row.id !== requestId));
      try {
        await ipc.dismissChannelRequest(requestId);
      } catch {
        // Nothing to say: the note is gone from this screen and the re-read below
        // is what decides whether it stays gone.
      } finally {
        refreshRequests();
      }
    },
    [refreshRequests],
  );

  return {
    channels,
    channelsLoaded: loaded,
    busy,
    checking,
    error,
    notice,
    statuses,
    pairings,
    pairing,
    lastRemoteTurn,
    pendingRequests,
    refreshChannels,
    refreshRequests,
    handleDismissRequest,
    handleAdd,
    handleRemove,
    handleSaveToken,
    handleConnect,
    handleSetEnabled,
    handleSetOnWake,
    handleBeginPairing,
    handleCancelPairing,
    handleRevokePairing,
  };
}

export type ChannelsCardState = ReturnType<typeof useChannels>;
