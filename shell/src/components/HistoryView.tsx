// Conversation history — a full-width list of past conversations (design-doc
// §7.1's calm everyday-utility idiom: sharp corners, hairline separators, plain
// copy, no icons). Each row opens that conversation back into the thread.
//
// This view is display + navigation only; it holds no conversation state of its
// own. App owns the list and the "which one is current" marker.

import { useEffect } from "react";
import type { ConversationSummary } from "../types/ui";

interface Props {
  conversations: ConversationSummary[];
  /** The open conversation, if App knows it yet (null = the launch one). */
  currentConversationId: string | null;
  onOpen: (id: string) => void;
  onBack: () => void;
}

export function HistoryView({ conversations, currentConversationId, onOpen, onBack }: Props) {
  // Escape returns to the chat, matching the drawer/dialog convention.
  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onBack();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onBack]);

  return (
    <div className="thread-scroll min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto w-full max-w-3xl px-6 py-6">
        <h2 className="mb-4 text-[10.5px] font-semibold uppercase tracking-[0.1em] text-faint">
          Your conversations
        </h2>

        {conversations.length === 0 ? (
          <p className="py-16 text-center text-base text-muted">No conversations yet.</p>
        ) : (
          <ul>
            {conversations.map((c) => {
              const isCurrent = currentConversationId != null && c.id === currentConversationId;
              return (
                <li key={c.id}>
                  <button
                    type="button"
                    onClick={() => onOpen(c.id)}
                    className={
                      "flex w-full items-baseline justify-between gap-4 rounded px-3 py-2.5 text-left hover:bg-hair " +
                      (isCurrent ? "bg-fern-tint" : "")
                    }
                  >
                    <span className="flex min-w-0 items-baseline gap-2.5">
                      <span className="truncate text-[13px] text-ink">{c.title}</span>
                      {isCurrent && (
                        <span className="shrink-0 text-[10.5px] font-semibold uppercase tracking-[0.1em] text-fern-deep">
                          Current
                        </span>
                      )}
                    </span>
                    <span className="shrink-0 font-mono text-xs text-muted">
                      {formatStarted(c.startedAt)} · {c.messageCount}{" "}
                      {c.messageCount === 1 ? "message" : "messages"}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}

// Compact, human date from epoch SECONDS: today → clock time (14:32); this year
// → "12 Jun"; earlier → "12 Jun 2025". Tiny and local — no date dependency.
function formatStarted(startedAtSeconds: number): string {
  if (!startedAtSeconds) return "";
  const d = new Date(startedAtSeconds * 1000);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();

  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  if (sameDay) {
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    return `${hh}:${mm}`;
  }

  const MONTHS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];
  const day = d.getDate();
  const month = MONTHS[d.getMonth()];
  if (d.getFullYear() === now.getFullYear()) return `${day} ${month}`;
  return `${day} ${month} ${d.getFullYear()}`;
}
