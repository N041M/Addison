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

import type { ConversationSummary } from "../types/ui";
import { BellLogo } from "./BellLogo";

interface Props {
  conversations: ConversationSummary[];
  /** The open conversation, or null for the not-yet-listed launch conversation. */
  currentConversationId: string | null;
  onOpenConversation: (id: string) => void;
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
   * "static" is the desktop left column (216px, collapsible). "drawer" is the
   * narrow-window slide-over (fills its 280px MobileDrawer, always full — the
   * collapse `«` control is hidden, and a safe-area top inset is added for a
   * phone status bar). Same component either way — never a fork.
   */
  variant?: "static" | "drawer";
}

export function Sidebar({
  conversations,
  currentConversationId,
  onOpenConversation,
  onNewChat,
  newChatDisabled,
  screen,
  onOpenSettings,
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
      {/* Wordmark. */}
      <div className="flex items-center px-[18px] pb-[14px]">
        <span className="flex items-center gap-2 text-ink">
          <BellLogo size={17} className="text-fern" />
          <span className="text-base font-bold tracking-logo">Addison</span>
        </span>
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
        />
        <ConversationGroup
          label="Earlier"
          rows={earlier}
          currentConversationId={currentConversationId}
          onOpen={onOpenConversation}
        />
      </nav>

      {/* Pinned bottom: Settings + profile label. A hairline separates it from
          the conversation list; the profile line shares the Settings label's
          text indent (2px bar + 12px padding) so the two read as one block. */}
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
}: {
  label: string;
  rows: ConversationSummary[];
  currentConversationId: string | null;
  onOpen: (id: string) => void;
}) {
  if (rows.length === 0) return null;
  return (
    <div>
      <p className="mx-[18px] mb-1.5 mt-4 text-label font-semibold uppercase tracking-caps-wide text-faint">
        {label}
      </p>
      {rows.map((c) => {
        const active = currentConversationId != null && c.id === currentConversationId;
        return (
          <button
            key={c.id}
            type="button"
            onClick={() => onOpen(c.id)}
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
      })}
    </div>
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
