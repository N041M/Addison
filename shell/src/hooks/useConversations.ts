// Conversations — the sidebar list, the current conversation's id + title, and
// the new/open flows. Extracted from App.tsx as a mechanical move: the state,
// the ref-sync effect, and the handlers are unchanged. Turn-owned pieces the
// handlers touch (busy guard, transient reset, the thread itself) are passed in.

import { useEffect, useRef, useState, type Dispatch, type SetStateAction } from "react";
import type { ConversationSummary, DisplayMessage } from "../types/ui";
import { ipc, isEngineConnected } from "../ipc/client";
import { WELCOME } from "./useTurn";

interface UseConversationsArgs {
  connected: boolean;
  /** True while a turn runs or a permission prompt is open (App's controlsBusy). */
  controlsBusy: boolean;
  /** App's resetTransientState — clears per-turn/per-conversation transients. */
  resetTransientState: () => void;
  /** The thread setter, from useTurn. */
  setMessages: Dispatch<SetStateAction<DisplayMessage[]>>;
  setScreen: (screen: "chat" | "settings") => void;
  setStatusBanner: (text: string | null) => void;
}

export function useConversations({
  connected,
  controlsBusy,
  resetTransientState,
  setMessages,
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
        setMessages([WELCOME]);
        setCurrentConversationId(id);
        setConversationTitle(null);
        setScreen("chat");
        // The new (still empty) conversation may not be in the list until its
        // first turn; refresh anyway so an existing row is reconciled.
        refreshConversations();
      })
      .catch(() => setStatusBanner("Couldn't start a new conversation."));
  }

  function handleOpenConversation(id: string) {
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

  return {
    conversations,
    currentConversationId,
    conversationTitle,
    refreshConversations,
    handleNewChat,
    handleOpenConversation,
  };
}

export type ConversationsState = ReturnType<typeof useConversations>;

// Coerce a stored row's role string to the display union. Loaded history holds
// only user + assistant rows; anything unexpected is shown as an assistant line
// rather than dropped.
function normalizeRole(role: string): DisplayMessage["role"] {
  return role === "user" || role === "assistant" || role === "tool" ? role : "assistant";
}
