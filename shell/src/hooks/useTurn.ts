// Turn lifecycle — the message thread, the running turn's working/activity
// state, the permission prompt, and Send/Retry/Stop. Extracted from App.tsx as
// a mechanical move: the logic — especially the currentTurnRef race guards that
// drop late results from stopped/superseded turns — is unchanged.

import { useRef, useState } from "react";
import type { ModelRole, PermissionRequest, ActivityUpdate } from "../types/protocol";
import type { DisplayMessage } from "../types/ui";
import { ipc, type RawError } from "../ipc/client";
import { asRecord } from "../lib/parse";

export const WELCOME: DisplayMessage = {
  id: "welcome",
  role: "assistant",
  content:
    "Hello — I'm Addison. Tell me what you'd like help with, and I'll ask first " +
    "before doing anything on your computer. You can always undo.",
};

interface UseTurnArgs {
  connected: boolean;
  setStatusBanner: (text: string | null) => void;
  /** The active role + picks, from useModelSelection. */
  selectedRole: ModelRole;
  selectedLocalModel?: string;
  selectedEffort?: string;
  effectiveLocalModel: (role: ModelRole, picked?: string) => string | undefined;
  effectiveCloudModel: () => string | undefined;
  /** From useWidgets: draft a widget after a turn that asked for one. */
  maybeProposeWidget: (userText: string) => void;
  /** From useConversations / useWidgets: post-turn refreshers. */
  refreshConversations: (adopt?: boolean) => void;
  refreshStats: () => void;
}

export function useTurn({
  connected,
  setStatusBanner,
  selectedRole,
  selectedLocalModel,
  selectedEffort,
  effectiveLocalModel,
  effectiveCloudModel,
  maybeProposeWidget,
  refreshConversations,
  refreshStats,
}: UseTurnArgs) {
  const [messages, setMessages] = useState<DisplayMessage[]>([WELCOME]);
  const [isWorking, setIsWorking] = useState(false);
  const [permission, setPermission] = useState<PermissionRequest | null>(null);

  const [currentActivity, setCurrentActivity] = useState<ActivityUpdate | null>(null);
  const [activities, setActivities] = useState<ActivityUpdate[]>([]);
  const [lastUserText, setLastUserText] = useState<string | null>(null);
  // Identifies the turn whose IPC result may still touch shared turn state (the
  // assistant message, isWorking, the activity line). Stop and every new turn
  // reassign it, so a result arriving late from an abandoned turn — the core has
  // no cancel, so its work keeps landing after Stop (see handleStop) — is dropped
  // instead of resurrecting stopped text or re-enabling the composer mid-turn.
  const currentTurnRef = useRef<string | null>(null);

  // --- Turn lifecycle -------------------------------------------------------
  async function runTurn(text: string, opts: { isRetry?: boolean } = {}) {
    const assistantId = uid();
    const userId = uid();
    currentTurnRef.current = assistantId;
    setMessages((prev) => {
      const base = opts.isRetry
        ? dropTrailingAssistant(prev)
        : [...prev, { id: userId, role: "user", content: text } as DisplayMessage];
      return [...base, { id: assistantId, role: "assistant", content: "", pending: true }];
    });

    setLastUserText(text);
    setActivities([]);
    setCurrentActivity(null);
    setPermission(null);
    setIsWorking(true);

    try {
      // Deliver the *effective* model for the active role. For "local", fall
      // back to the first model when the dropdown was never touched (the picker
      // shows it as selected). For cloud, send the picked model + its effort
      // level; effort never applies to local models (§4.1.1 B).
      const isLocal = selectedRole === "local";
      const modelId = isLocal
        ? effectiveLocalModel("local", selectedLocalModel)
        : effectiveCloudModel();
      const effort = isLocal ? undefined : selectedEffort;
      const res = await ipc.sendMessage(text, selectedRole, modelId, effort);
      // Stopped or superseded by a newer turn while we were waiting — drop this
      // result so it can't overwrite "(Stopped.)" or a later turn's answer.
      if (currentTurnRef.current !== assistantId) return;
      const finalText = extractFinalText(res);
      // The core's persisted ids: what "Rewind to here" must anchor on.
      const ids = asRecord(res);
      const userStoreId = typeof ids?.userMessageId === "string" ? ids.userMessageId : undefined;
      const assistantStoreId =
        typeof ids?.assistantMessageId === "string" ? ids.assistantMessageId : undefined;
      setMessages((prev) =>
        prev.map((m) => {
          if (m.id === assistantId) {
            return { ...m, pending: false, content: finalText ?? m.content, storeId: assistantStoreId };
          }
          if (m.id === userId) {
            return { ...m, storeId: userStoreId };
          }
          return m;
        }),
      );
      // Composer path: if the user asked Addison to build a widget, draft one from
      // the just-finished conversation (nothing is saved until they confirm).
      maybeProposeWidget(text);
    } catch (err) {
      // Same guard on the failure path: an abandoned turn's error must not
      // replace the stopped message or a newer turn's content.
      if (currentTurnRef.current !== assistantId) return;
      const message = err instanceof Error ? err.message : "Something went wrong.";
      // Developer-only: the client attaches the real exception text as `.raw`.
      // We keep it on the message; ChatThread renders it only when the
      // raw-diagnostics flag is on, so the plain message is all Simple ever sees.
      const raw = (err as RawError | undefined)?.raw;
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? {
                ...m,
                pending: false,
                failed: true,
                // The core and the IPC client both send complete plain-language
                // sentences with a next step — render them as-is, no re-wrapping.
                content: m.content || message,
                raw: typeof raw === "string" ? raw : undefined,
              }
            : m,
        ),
      );
    } finally {
      // Only the still-current turn clears the working/activity state; an
      // abandoned turn's cleanup would otherwise re-enable the composer and hide
      // the activity line while a newer turn is still running.
      if (currentTurnRef.current === assistantId) {
        currentTurnRef.current = null;
        setIsWorking(false);
        setCurrentActivity(null);
        // A turn just landed: refresh the sidebar so a new chat's auto-title
        // appears, and adopt the launch conversation as current if we didn't
        // know its id yet. Usage changed too, so refresh the token meter.
        refreshConversations(true);
        refreshStats();
      }
    }
  }

  function handleSend(text: string) {
    if (!connected) {
      setStatusBanner("Addison's engine isn't connected yet, so I can't reply.");
      return;
    }
    void runTurn(text);
  }

  function handleRetry() {
    if (!connected || isWorking || !lastUserText) return;
    void runTurn(lastUserText, { isRetry: true });
  }

  function handleStop() {
    // The v1 IPC contract has no core-side cancel method, so Stop halts the
    // webview turn: it stops accepting streamed text and re-enables the input.
    // Abandon the turn so its still-in-flight result can't land later and
    // overwrite the "(Stopped.)" message (the core keeps working regardless).
    currentTurnRef.current = null;
    setIsWorking(false);
    setCurrentActivity(null);
    setMessages((prev) =>
      prev.map((m) =>
        m.pending
          ? { ...m, pending: false, content: m.content || "(Stopped.)" }
          : m,
      ),
    );
  }

  // The turn-scoped half of App's resetTransientState (clearing a switched-away
  // conversation's in-flight state); App adds its own transient bits on top.
  function resetTurn() {
    currentTurnRef.current = null;
    setIsWorking(false);
    setActivities([]);
    setCurrentActivity(null);
    setPermission(null);
    setLastUserText(null);
  }

  return {
    messages,
    setMessages,
    isWorking,
    permission,
    setPermission,
    activities,
    setActivities,
    currentActivity,
    setCurrentActivity,
    lastUserText,
    handleSend,
    handleRetry,
    handleStop,
    resetTurn,
  };
}

export type TurnState = ReturnType<typeof useTurn>;

// ---------------------------------------------------------------------------
// Small pure helpers (moved with the turn logic from App.tsx).
// ---------------------------------------------------------------------------
function uid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `m-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function dropTrailingAssistant(list: DisplayMessage[]): DisplayMessage[] {
  const copy = [...list];
  while (copy.length && copy[copy.length - 1].role === "assistant") copy.pop();
  return copy;
}

function extractFinalText(result: unknown): string | null {
  const obj = asRecord(result);
  if (!obj) return typeof result === "string" ? result : null;
  if (typeof obj.text === "string") return obj.text;
  if (typeof obj.content === "string") return obj.content;
  const msg = asRecord(obj.message);
  if (msg && typeof msg.content === "string") return msg.content;
  return null;
}
