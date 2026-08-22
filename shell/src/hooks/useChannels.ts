// Messaging channels — the phone connections a person has saved (phase 1 of three;
// docs/messaging-channel-plan.md). This hook owns the list, the add/remove handlers,
// the token save, and the two transient lines the panel shows — a plain error (the
// core's own refusal sentence, or the shell's) and a plain notice. It mirrors
// useMcpServers, which is the surface this one is modelled on throughout.
//
// NOTHING HERE CONNECTS TO ANYTHING, because there is nothing in the app to connect
// with: no adapter, no poll loop, no pairing, and no network call on any path this
// hook can reach. Saving a connection stores a name and a kind; saving a token puts
// it in the OS keychain. That is the whole of phase 1, and the panel says so.
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
// independently refuses `channel.add` outside Developer.

import { useCallback, useEffect, useState } from "react";
import type { Channel, ChannelKind } from "../types/ui";
import {
  deleteChannelKey,
  ipc,
  isEngineConnected,
  storeChannelKey,
  subscribeCoreState,
} from "../ipc/client";

interface UseChannelsArgs {
  connected: boolean;
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

  const refreshChannels = useCallback(() => {
    if (!isEngineConnected()) return;
    ipc
      .listChannels()
      .then((rows) => {
        setChannels(rows);
        setLoaded(true);
      })
      .catch(() => {
        // Keep the last-known list rather than blanking the panel; still stop the
        // looking-for line.
        setLoaded(true);
      });
  }, []);

  useEffect(() => {
    refreshChannels();
    // Every "ready" is a fresh engine — re-read, like the other data hooks.
    return subscribeCoreState((state) => {
      if (state === "ready") refreshChannels();
    });
  }, [connected, refreshChannels]);

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
   * re-read afterwards anyway, because the row is the only thing that can report on
   * a token — and in phase 1 it honestly reports "unknown", since proving a token
   * works means asking Telegram, which is the next phase.
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

  return {
    channels,
    channelsLoaded: loaded,
    busy,
    error,
    notice,
    refreshChannels,
    handleAdd,
    handleRemove,
    handleSaveToken,
  };
}

export type ChannelsCardState = ReturnType<typeof useChannels>;
