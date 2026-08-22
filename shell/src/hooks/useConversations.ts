// Conversations — the sidebar list, the current conversation's id + title, and
// the new/open flows. Extracted from App.tsx as a mechanical move: the state,
// the ref-sync effect, and the handlers are unchanged. Turn-owned pieces the
// handlers touch (busy guard, transient reset, the thread itself) are passed in.

import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import type { ConversationSummary, DisplayMessage, ThreadContinuation } from "../types/ui";
import type { ActivityUpdate } from "../types/protocol";
import { ipc, isEngineConnected } from "../ipc/client";

interface UseConversationsArgs {
  connected: boolean;
  /** True while a turn runs or a permission prompt is open (App's controlsBusy). */
  controlsBusy: boolean;
  /**
   * True while the engine is BLOCKED on a consent card. Separate from
   * `controlsBusy`, which it is one half of: leaving a running turn merely strands
   * its result (the turn ref drops it), but leaving a pending CARD destroys the
   * only copy of a question the engine is still waiting on — `resetTransientState`
   * clears it, nothing re-sends it, and nothing times out. The worker is blocked,
   * so the next message queues behind it and the app looks dead.
   */
  permissionPending: boolean;
  /** App's resetTransientState — clears per-turn/per-conversation transients. */
  resetTransientState: () => void;
  /** The thread setter, from useTurn. */
  setMessages: Dispatch<SetStateAction<DisplayMessage[]>>;
  /** The work-panel setter, from useTurn: a reopened chat gets its last turn's
   * steps back (KNOWN-BUGS #5), which is also what puts "Save as routine" back. */
  setActivities: Dispatch<SetStateAction<ActivityUpdate[]>>;
  setScreen: (screen: "chat" | "settings") => void;
  setStatusBanner: (text: string | null) => void;
}

export function useConversations({
  connected,
  controlsBusy,
  permissionPending,
  resetTransientState,
  setMessages,
  setActivities,
  setScreen,
  setStatusBanner,
}: UseConversationsArgs) {
  // Conversations. The core mints a conversation per launch, but the frontend
  // doesn't learn its id until it starts or loads one — `null` means "the launch
  // conversation", and the sidebar marks no row current until an id is known. The
  // list lives permanently in the sidebar (it replaced the old HistoryView): it's
  // loaded on mount and refreshed after each completed turn + after new/load, so a
  // new chat's auto-title appears without a reload.
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [currentConversationId, setCurrentConversationId] = useState<string | null>(null);
  // The active conversation's title, shown in the chat header. Null → the
  // "New conversation" fallback (an untitled or not-yet-titled chat).
  const [conversationTitle, setConversationTitle] = useState<string | null>(null);
  // The open chat's boundary, when it has one: the chat it continues and the
  // summary it was seeded with, both straight off the stored row. Null for an
  // ordinary chat, and cleared by a new one.
  const [continuation, setContinuation] = useState<ThreadContinuation | null>(null);
  // A stable mirror of the current id so the post-turn list refresh (which runs
  // in an async `finally`) reads the up-to-date value, not a stale closure.
  const currentConversationIdRef = useRef<string | null>(null);
  useEffect(() => {
    currentConversationIdRef.current = currentConversationId;
  }, [currentConversationId]);

  // Refresh the sidebar's conversation list. When `adopt` is set and we don't yet
  // know the current conversation's id (the launch conversation, whose id the
  // frontend never learns until a turn lands), take the newest row as current —
  // that's the chat a just-finished turn belongs to — so the sidebar highlights
  // it and the header shows its freshly minted auto-title. Otherwise just refresh
  // the current row's title in place.
  function refreshConversations(adopt = false) {
    if (!isEngineConnected()) return;
    ipc
      .listConversations()
      .then((list) => {
        setConversations(list);
        const currentId = currentConversationIdRef.current;
        if (currentId != null) {
          const match = list.find((c) => c.id === currentId);
          if (match) setConversationTitle(match.title);
        } else if (adopt && list.length > 0) {
          setCurrentConversationId(list[0].id);
          setConversationTitle(list[0].title);
        }
      })
      .catch(() => {
        /* leave the sidebar list as-is if we can't read it */
      });
  }

  function handleNewChat() {
    if (!connected || controlsBusy) return;
    ipc
      .newConversation()
      .then((id) => {
        resetTransientState();
        // A new chat starts genuinely empty, which is what puts the greeting
        // stack on screen (ChatThread's empty state).
        setMessages([]);
        setCurrentConversationId(id);
        setConversationTitle(null);
        setContinuation(null); // a fresh chat continues nothing
        setScreen("chat");
        // The new (still empty) conversation may not be in the list until its
        // first turn; refresh anyway so an existing row is reconciled.
        refreshConversations();
      })
      .catch(() => setStatusBanner("Couldn't start a new conversation."));
  }

  /** The one thing that must not be thrown away by a navigation. Says so plainly,
   * in the card's own terms, rather than dead-ending the click. */
  const ANSWER_FIRST = "Answer Addison's question first — it's still waiting for you.";

  function handleOpenConversation(id: string) {
    // `handleNewChat` has always refused while `controlsBusy`; opening another chat
    // never did, and the difference was invisible because the sidebar only disables
    // the New-chat control. Refusing on the PENDING CARD alone rather than on
    // `controlsBusy`: leaving a running turn is an ordinary, recoverable thing to
    // do, and leaving a card is not (see `permissionPending` above).
    if (permissionPending) {
      setStatusBanner(ANSWER_FIRST);
      return;
    }
    ipc
      .loadConversation(id)
      .then((loaded) => {
        const rows: DisplayMessage[] = loaded.messages.map((row) => ({
          id: row.id,
          storeId: row.id,
          role: normalizeRole(row.role),
          content: row.content,
        }));
        resetTransientState();
        setMessages(rows);
        // AFTER the reset, which clears the previous chat's steps. The panel is
        // per-turn state, so this is the reopened chat's last turn and nothing
        // older — the same thing the person was looking at when they closed it.
        setActivities(loaded.work);
        // The durable half of §4.8's "boundary marker in the thread": the note
        // said when the chat was condensed is long gone (per-turn channel), and
        // this is the stored row saying the same thing every time it is opened.
        setContinuation(
          loaded.continuedFrom
            ? { fromId: loaded.continuedFrom, summary: loaded.summary }
            : null,
        );
        setCurrentConversationId(loaded.conversationId || id);
        setConversationTitle(
          loaded.title ?? conversations.find((c) => c.id === (loaded.conversationId || id))?.title ?? null,
        );
        setScreen("chat");
      })
      .catch((err) => {
        // Surface the plain-language reason (e.g. the core's "Couldn't find that
        // conversation.").
        setStatusBanner(
          err instanceof Error ? err.message : "Couldn't open that conversation.",
        );
      });
  }

  // Rename a chat (double-click its title in the sidebar). Optimistic: update the
  // row (and the header, if it's the open chat) immediately, then persist. On
  // failure, revert to the pre-rename title and surface the plain-language reason.
  function handleRenameConversation(id: string, rawTitle: string) {
    const title = rawTitle.trim();
    const before = conversations.find((c) => c.id === id);
    if (!title || (before && title === before.title)) return; // blank or unchanged → no-op
    setConversations((list) => list.map((c) => (c.id === id ? { ...c, title } : c)));
    if (currentConversationIdRef.current === id) setConversationTitle(title);
    ipc
      .renameConversation(id, title)
      .then((res) => {
        if (!res.ok) throw new Error(res.error || "Couldn't rename that chat.");
        // Adopt the core's canonical (trimmed/capped) title if it differs.
        if (res.title && res.title !== title) {
          const canonical = res.title;
          setConversations((list) => list.map((c) => (c.id === id ? { ...c, title: canonical } : c)));
          if (currentConversationIdRef.current === id) setConversationTitle(canonical);
        }
      })
      .catch((err) => {
        setConversations((list) =>
          list.map((c) => (c.id === id && before ? { ...c, title: before.title } : c)),
        );
        if (currentConversationIdRef.current === id) setConversationTitle(before ? before.title : null);
        setStatusBanner(err instanceof Error ? err.message : "Couldn't rename that chat.");
      });
  }

  return {
    conversations,
    currentConversationId,
    conversationTitle,
    continuation,
    refreshConversations,
    handleNewChat,
    handleOpenConversation,
    handleRenameConversation,
  };
}

// Coerce a stored row's role string to the display union. Loaded history holds
// only user + assistant rows; anything unexpected is shown as an assistant line
// rather than dropped.
function normalizeRole(role: string): DisplayMessage["role"] {
  return role === "user" || role === "assistant" || role === "tool" ? role : "assistant";
}
