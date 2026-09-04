// Your documents — the knowledge base's state (knowledge.list / add / reindex /
// remove; knowledge retrieval, phase 3 of three). This hook owns the list of
// documents a person has given Addison, the add/update/remove handlers, and the
// two transient lines the panel shows — a plain error (the core's own refusal
// sentence) and a plain notice (a removal landed). It mirrors useMcpServers.
//
// NOTHING HERE READS A FILE. Adding and re-reading both go through a picker the
// core opens on the person's screen; this window sends `{}` or an id and waits.
// So there is no path in this module, no file content, and no way for the webview
// to decide which document gets read.
//
// `busy` IS ONE FLAG FOR THE WHOLE PANEL, deliberately unlike useMcpServers'
// per-row `checking` set. A check reaches somebody else's server and the other
// rows stay usable; an add or an update puts a MODAL PICKER on the screen and
// then runs a local embedding pass. Nothing else on the panel can be pressed
// while that dialog is up, so a panel-wide busy state is the honest drawing of
// what is actually happening — and it is what lets the panel say so in a sentence
// rather than leaving a person watching a page that does nothing for a minute.
//
// A CLOSED PICKER IS NOT A FAILURE. `{ok:false, cancelled:true}` sets neither
// error nor notice: the person said "never mind", and answering that with a line
// explaining what went wrong would be the app arguing with a decision.
//
// A restore never disturbs this list. The three knowledge tables are excluded
// from restore points (owner decision 4, 2026-08-24), so — unlike the tool
// servers, the channels and the automations — there is nothing here for App's
// `onRestored` closure to re-read.

import { useCallback, useEffect, useState } from "react";
import type { KnowledgeDocument } from "../types/ui";
import { ipc, isEngineConnected, subscribeCoreState } from "../ipc/client";

interface UseKnowledgeArgs {
  connected: boolean;
}

// --- The last-resort lines ---------------------------------------------------
// Said only when the core gave no sentence of its own (a dropped connection, a
// malformed answer). Every real refusal arrives already written in plain words by
// the core, and is printed verbatim in preference to any of these.
const ADD_FAILED = "Addison couldn't add that document just now.";
const REINDEX_FAILED = "Addison couldn't read that document again just now.";
const REMOVE_FAILED = "Addison couldn't remove that document just now.";

export function useKnowledge({ connected }: UseKnowledgeArgs) {
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  // "not loaded yet" vs "loaded" — a slow first fetch must not render an empty,
  // ambiguous "No documents yet." over somebody's indexed library.
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  // The last refusal, in the core's own already-plain words. Cleared when the
  // next action starts.
  const [error, setError] = useState<string | null>(null);
  // The last removal's plain outcome line. Stays put rather than fading.
  const [notice, setNotice] = useState<string | null>(null);

  const refreshDocuments = useCallback(() => {
    if (!isEngineConnected()) return;
    ipc
      .listKnowledgeDocuments()
      .then((rows) => {
        setDocuments(rows);
        setLoaded(true);
      })
      .catch(() => {
        // Keep the last-known list rather than blanking the panel; still stop the
        // looking-for line.
        setLoaded(true);
      });
  }, []);

  useEffect(() => {
    refreshDocuments();
    // Every "ready" is a fresh engine — re-read, like the other data hooks. It is
    // also the one moment `onDisk` can change without anybody pressing anything:
    // the answer is computed while the list is answered, so a file edited in
    // another program shows as changed the next time this list is read.
    return subscribeCoreState((state) => {
      if (state === "ready") refreshDocuments();
    });
  }, [connected, refreshDocuments]);

  /** Add a document: the core opens the picker, reads what the person chooses,
   * and indexes it. A cancelled picker is silent — see the note at the top. */
  const handleAdd = useCallback(async (): Promise<void> => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await ipc.addKnowledgeDocument();
      if (!res.ok && !res.cancelled) setError(res.error ?? ADD_FAILED);
    } catch {
      setError(ADD_FAILED);
    } finally {
      setBusy(false);
      refreshDocuments();
    }
  }, [refreshDocuments]);

  /** Read a document again — the panel's "Update" (the file changed on disk) and
   * its "Try again" (the last read failed) are the same call. The picker opens on
   * that file and the person confirms it, which is what keeps a re-read a thing
   * they did rather than a thing the app did with a stored path. */
  const handleReindex = useCallback(
    async (id: string): Promise<void> => {
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        const res = await ipc.reindexKnowledgeDocument(id);
        if (!res.ok && !res.cancelled) setError(res.error ?? REINDEX_FAILED);
      } catch {
        setError(REINDEX_FAILED);
      } finally {
        setBusy(false);
        refreshDocuments();
      }
    },
    [refreshDocuments],
  );

  /** Forget a document. Removing only ever takes something away, so it goes
   * straight through — the panel's own "Really remove?" second press is the
   * confirmation, and the sentence beside it is where the person is told this one
   * cannot be undone by a restore point. */
  const handleRemove = useCallback(
    async (id: string, displayName: string): Promise<void> => {
      setBusy(true);
      setError(null);
      setNotice(null);
      try {
        const res = await ipc.removeKnowledgeDocument(id);
        if (res.ok) {
          setNotice(`Addison has forgotten ${displayName}. The file itself is untouched.`);
        } else {
          setError(res.error ?? REMOVE_FAILED);
        }
      } catch {
        setError(REMOVE_FAILED);
      } finally {
        setBusy(false);
        refreshDocuments();
      }
    },
    [refreshDocuments],
  );

  return {
    documents,
    documentsLoaded: loaded,
    busy,
    error,
    notice,
    refreshDocuments,
    handleAdd,
    handleReindex,
    handleRemove,
  };
}

export type KnowledgeCardState = ReturnType<typeof useKnowledge>;
