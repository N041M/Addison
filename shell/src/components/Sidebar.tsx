// Conversation sidebar — the left column of the Fern app shell (design-brief-fern
// README §1, handoff §1). `side` background, a 1px `line` right border, full
// height. Holds the wordmark, the "New chat" button, the grouped conversation
// list (TODAY / EARLIER), and — pinned to the bottom — Settings and the current
// profile label. Always present above md (no desktop hide/collapse — owner
// request 2026-07-19); below md it appears only as the slide-over drawer.
//
// This is the permanent home of the conversation list — it replaces the old
// full-window HistoryView. Selecting a row loads that conversation; the active
// row (currentConversationId, or Settings when the settings screen is open) gets
// a `hair` background and a 2px fern left bar.

import { useEffect, useRef, useState } from "react";
import type { ConversationSummary } from "../types/ui";
import { BellLogo } from "./BellLogo";

interface Props {
  conversations: ConversationSummary[];
  /** The open conversation, or null for the not-yet-listed launch conversation. */
  currentConversationId: string | null;
  onOpenConversation: (id: string) => void;
  /** Rename a chat — double-clicking its title opens an inline editor. */
  onRenameConversation: (id: string, title: string) => void;
  onNewChat: () => void;
  newChatDisabled: boolean;
  /** Which in-window screen is showing; drives the Settings item's active state. */
  screen: "chat" | "settings";
  onOpenSettings: () => void;
  /** Plain label for the active profile, e.g. "Simple profile". */
  profileLabel: string;
  /**
   * OPEN/Developer mode only: a dim, mono suffix (e.g. "open") appended to the
   * profile label — the one quiet acknowledgement that Addison can act more
   * freely here. Absent (undefined) in SAFE mode.
   */
  modeNote?: string;
  /**
   * Closes the mobile drawer (the `«` arrow in its header). Drawer variant only;
   * undefined on desktop, where the sidebar is always present and the arrow
   * doesn't render.
   */
  onCloseDrawer?: () => void;
  /**
   * "static" is the desktop left column (216px, always present). "drawer" is the
   * narrow-window slide-over (fills its 280px MobileDrawer, with a `«` close
   * arrow and a safe-area top inset for a phone status bar). Same component
   * either way — never a fork.
   */
  variant?: "static" | "drawer";
}

export function Sidebar({
  conversations,
  currentConversationId,
  onOpenConversation,
  onRenameConversation,
  onNewChat,
  newChatDisabled,
  screen,
  onOpenSettings,
  onCloseDrawer,
  profileLabel,
  modeNote,
  variant = "static",
}: Props) {
  const isDrawer = variant === "drawer";

  const { today, earlier } = groupConversations(conversations);

  return (
    <aside
      className={
        "flex flex-col border-r border-line bg-side py-4 " +
        (isDrawer
          ? "h-full w-full pt-[calc(env(safe-area-inset-top)+16px)]"
          : "w-[216px] shrink-0")
      }
    >
      {/* Wordmark, and — in the drawer — a `«` arrow to slide it closed. */}
      <div className="flex items-center justify-between px-[18px] pb-[14px]">
        <span className="flex items-center gap-2 text-ink">
          <BellLogo size={17} className="text-fern" />
          <span className="text-base font-bold tracking-logo">Addison</span>
        </span>
        {isDrawer && onCloseDrawer && (
          <button
            type="button"
            onClick={onCloseDrawer}
            aria-label="Close menu"
            className="-mr-2 flex h-11 w-11 items-center justify-center text-glyph text-ink-soft transition-colors hover:text-ink"
          >
            «
          </button>
        )}
      </div>

      {/* New chat — outlined, ownable/actionable (6px radius). */}
      <button
        type="button"
        onClick={onNewChat}
        disabled={newChatDisabled}
        className="mx-[14px] flex items-center gap-[7px] rounded-sm border border-line bg-surface px-3 py-2 text-left text-control font-semibold text-fern-deep hover:border-muted disabled:cursor-not-allowed disabled:opacity-50 max-md:min-h-[44px] max-md:text-row"
      >
        ＋ New chat
      </button>

      {/* Conversation list, grouped. Scrolls independently; the bottom block is
          pinned via margin-top:auto. */}
      <nav className="mt-2 flex min-h-0 flex-1 flex-col overflow-y-auto">
        <ConversationGroup
          label="Today"
          rows={today}
          currentConversationId={currentConversationId}
          onOpen={onOpenConversation}
          onRename={onRenameConversation}
        />
        <ConversationGroup
          label="Earlier"
          rows={earlier}
          currentConversationId={currentConversationId}
          onOpen={onOpenConversation}
          onRename={onRenameConversation}
        />
      </nav>

      {/* Pinned bottom: Settings + profile label. A hairline separates it from
          the conversation list; the profile line shares the Settings label's
          text indent (2px bar + 12px padding) so the block reads as one. */}
      <div className="mt-auto flex flex-col border-t border-line px-[14px] pb-3.5 pt-2.5">
        <button
          type="button"
          onClick={onOpenSettings}
          className={
            "flex items-center gap-2 border-l-2 px-3 py-2 text-left text-control font-medium text-ink-soft max-md:min-h-[44px] max-md:text-row " +
            (screen === "settings"
              ? "border-fern bg-hair"
              : "border-transparent bg-transparent hover:bg-hair/50")
          }
        >
          Settings
        </button>
        <p className="pl-[14px] pr-3 pt-1 text-xs leading-relaxed text-faint">
          {profileLabel}
          {modeNote && <span className="ml-1.5 font-mono text-tick text-faint">· {modeNote}</span>}
        </p>
      </div>
    </aside>
  );
}

function ConversationGroup({
  label,
  rows,
  currentConversationId,
  onOpen,
  onRename,
}: {
  label: string;
  rows: ConversationSummary[];
  currentConversationId: string | null;
  onOpen: (id: string) => void;
  onRename: (id: string, title: string) => void;
}) {
  if (rows.length === 0) return null;
  return (
    <div>
      <p className="mx-[18px] mb-1.5 mt-4 text-label font-semibold uppercase tracking-caps-wide text-faint">
        {label}
      </p>
      {rows.map((c) => (
        <ConversationRow
          key={c.id}
          conversation={c}
          active={currentConversationId != null && c.id === currentConversationId}
          onOpen={onOpen}
          onRename={onRename}
        />
      ))}
    </div>
  );
}

// One conversation row. Single-click opens it; double-clicking the title swaps
// it for an inline editor (Enter/blur commits, Escape cancels). The editor keeps
// the row's exact geometry (same border/padding/text) so nothing shifts.
function ConversationRow({
  conversation: c,
  active,
  onOpen,
  onRename,
}: {
  conversation: ConversationSummary;
  active: boolean;
  onOpen: (id: string) => void;
  onRename: (id: string, title: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(c.title);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  function startEditing() {
    setDraft(c.title);
    setEditing(true);
  }
  function commit() {
    setEditing(false);
    onRename(c.id, draft); // the hook no-ops on blank/unchanged
  }
  function cancel() {
    setEditing(false);
    setDraft(c.title);
  }

  if (editing) {
    return (
      <input
        ref={inputRef}
        value={draft}
        maxLength={120}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            commit();
          } else if (e.key === "Escape") {
            e.preventDefault();
            cancel();
          }
        }}
        aria-label="Rename chat"
        className="block w-full border-l-2 border-fern bg-surface px-4 py-2 text-left text-control text-ink outline-none max-md:py-3.5 max-md:text-row"
      />
    );
  }

  return (
    <button
      type="button"
      onClick={() => onOpen(c.id)}
      onDoubleClick={startEditing}
      title={c.title}
      className={
        // Weight stays constant across states (a bold swap on select would
        // shift the truncation point); the active cue is the fern rule +
        // hair fill + darker ink. The 2px left border is pre-reserved
        // (transparent when inactive) so selecting never nudges the text.
        "block w-full overflow-hidden text-ellipsis whitespace-nowrap border-l-2 px-4 py-2 text-left text-control transition-colors max-md:py-3.5 max-md:text-row " +
        (active
          ? "border-fern bg-hair text-ink"
          : "border-transparent bg-transparent text-muted hover:bg-hair/50")
      }
    >
      {c.title}
    </button>
  );
}

// Split summaries into today vs. everything earlier. `startedAt` is epoch
// SECONDS; a zero/absent value falls into "Earlier" rather than being dropped.
function groupConversations(conversations: ConversationSummary[]): {
  today: ConversationSummary[];
  earlier: ConversationSummary[];
} {
  const now = new Date();
  const today: ConversationSummary[] = [];
  const earlier: ConversationSummary[] = [];
  for (const c of conversations) {
    if (c.startedAt && isSameDay(new Date(c.startedAt * 1000), now)) today.push(c);
    else earlier.push(c);
  }
  return { today, earlier };
}

function isSameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}
