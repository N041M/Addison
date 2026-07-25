// The left column — 212px, no background of its own, no border (docs/design-brief-dark
// §4). Top to bottom: a "Workspace" block (Tools · Snapshots, each with a mono
// hint), "＋ New chat", the real conversations grouped Today / Earlier, and —
// pinned to the bottom — Settings with the profile note under it.
//
// It is the permanent home of the conversation list. Selecting a row loads that
// conversation; the active row (and Settings, while that surface is open) is
// marked with a 2px accent LEFT RAIL and brighter ink — never a filled
// background, which is the shape rule the whole redesign turns on.
//
// GROUP EXPAND / COLLAPSE is ported faithfully from the prototype
// (expandGroup / collapseGroup / cancelCollapse), because the tempo is the
// design:
//   * expand animates ONLY the newly revealed rows (fadeRise .3s); the three
//     that were already there never re-animate, so nothing flickers.
//   * collapse plays fadeDrop .3s on the rows beyond the first three and commits
//     the state at ~290ms, so the rows leave before they disappear.
//   * re-clicking mid-collapse CANCELS it — the rows fade back in and no stale
//     state lands. Rapid toggling can therefore never queue a collapse that
//     arrives after the person changed their mind.
//
// Real data only: the buckets come from each conversation's own `startedAt`, and
// a conversation without a usable timestamp is shown under "Earlier" with no
// time rather than being dated with a guess (IMPLEMENTATION.md, standing rule 1).

import { useEffect, useRef, useState } from "react";
import type { ConversationSummary, View } from "../types/ui";
import { isMotionEnabled } from "../lib/scramble";

interface Props {
  conversations: ConversationSummary[];
  /** The open conversation, or null for the not-yet-listed launch conversation. */
  currentConversationId: string | null;
  onOpenConversation: (id: string) => void;
  /** Rename a chat — double-clicking its title opens an inline editor. */
  onRenameConversation: (id: string, title: string) => void;
  onNewChat: () => void;
  newChatDisabled: boolean;
  /** Which view is showing; drives the active rail on Tools/Snapshots/Settings. */
  view: View;
  onOpenSettings: () => void;
  /** Opens the Tools surface (and, when it's already open, returns to chat). */
  onOpenTools?: () => void;
  /** Opens the Snapshots surface (same toggle behaviour). */
  onOpenSnapshots?: () => void;
  /** Mono hint beside "Tools" — the trusted-folder count, else the policy mode. */
  toolsHint?: string;
  /** Mono hint beside "Snapshots" — how many restore points exist. */
  snapshotsHint?: string;
  /** Plain label for the active profile, e.g. "Simple profile". */
  profileLabel: string;
  /**
   * OPEN mode only: a dim suffix ("open") appended to the profile note — the one
   * quiet acknowledgement that the safety posture is different. Nothing louder.
   */
  modeNote?: string;
  /** Closes the mobile drawer (`«`). Drawer variant only. */
  onCloseDrawer?: () => void;
  /**
   * "static" is the desktop column (212px). "drawer" is the narrow-window
   * slide-over: same component, filling its MobileDrawer, with a close arrow and
   * a safe-area top inset. Never a fork.
   */
  variant?: "static" | "drawer";
}

/** How many rows a group shows before "N more…". */
const COLLAPSED_ROWS = 3;
/** Matches the fadeDrop duration the exiting rows play. */
const COLLAPSE_COMMIT_MS = 290;

export function Sidebar({
  conversations,
  currentConversationId,
  onOpenConversation,
  onRenameConversation,
  onNewChat,
  newChatDisabled,
  view,
  onOpenSettings,
  onOpenTools,
  onOpenSnapshots,
  toolsHint,
  snapshotsHint,
  profileLabel,
  modeNote,
  onCloseDrawer,
  variant = "static",
}: Props) {
  const isDrawer = variant === "drawer";
  const rootRef = useRef<HTMLDivElement>(null);

  // Which groups are expanded, plus the machinery that makes expand/collapse
  // interruptible. The refs hold what must survive a render without causing one.
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const collapseTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const collapsingKey = useRef<string | null>(null);
  const skipRowAnim = useRef(false);
  const pendingExpand = useRef<string | null>(null);
  const seenExpanded = useRef<string | undefined>(undefined);

  useEffect(() => {
    // Cancel a pending collapse if the sidebar goes away mid-animation (the
    // drawer closing, or the window growing past the breakpoint).
    return () => {
      if (collapseTimer.current) clearTimeout(collapseTimer.current);
    };
  }, []);

  const buckets = bucketConversations(conversations);
  const groups = ([["today", "Today"], ["earlier", "Earlier"]] as const)
    .map(([key, label]) => ({ key, label, ids: buckets[key] }))
    .filter((g) => g.ids.length > 0);

  function groupBox(key: string): HTMLElement | null {
    return rootRef.current?.querySelector<HTMLElement>(`[data-group="${key}"]`) ?? null;
  }

  // Restart a CSS animation on an element that may already carry one.
  function replay(el: HTMLElement, animation: string) {
    el.style.animation = "none";
    el.getBoundingClientRect(); // force reflow so the restart is seen
    el.style.animation = animation;
  }

  function expandGroup(key: string) {
    if (collapseTimer.current) clearTimeout(collapseTimer.current);
    collapseTimer.current = null;
    collapsingKey.current = null;
    skipRowAnim.current = true;
    pendingExpand.current = key;
    setExpanded((prev) => ({ ...prev, [key]: true }));
  }

  function collapseGroup(key: string) {
    const commit = () => {
      skipRowAnim.current = true;
      setExpanded((prev) => ({ ...prev, [key]: false }));
    };
    const box = groupBox(key);
    if (!box || !isMotionEnabled()) return commit();
    const exiting = Array.from(box.children).slice(COLLAPSED_ROWS) as HTMLElement[];
    if (exiting.length === 0) return commit();
    exiting.forEach((el) => replay(el, "fadeDrop .3s ease both"));
    if (collapseTimer.current) clearTimeout(collapseTimer.current);
    collapsingKey.current = key;
    collapseTimer.current = setTimeout(() => {
      collapseTimer.current = null;
      collapsingKey.current = null;
      commit();
    }, COLLAPSE_COMMIT_MS);
  }

  function cancelCollapse(key: string) {
    if (collapseTimer.current) clearTimeout(collapseTimer.current);
    collapseTimer.current = null;
    collapsingKey.current = null;
    skipRowAnim.current = false;
    const box = groupBox(key);
    if (box && isMotionEnabled()) {
      Array.from(box.children).forEach((el) =>
        replay(el as HTMLElement, "fadeRise .3s ease both"),
      );
    }
  }

  function toggleGroup(key: string, count: number, isExpanded: boolean) {
    if (count <= COLLAPSED_ROWS) return;
    // Mid-collapse, the same click means "no, keep them".
    if (collapseTimer.current && collapsingKey.current === key) return cancelCollapse(key);
    if (isExpanded) collapseGroup(key);
    else expandGroup(key);
  }

  // Post-commit animation, mirroring the prototype's componentDidUpdate: only the
  // rows a person just revealed move, and a committed collapse leaves no inline
  // animation behind on the rows that stayed.
  useEffect(() => {
    const key = JSON.stringify(expanded);
    const first = seenExpanded.current === undefined;
    seenExpanded.current = key;
    const skip = skipRowAnim.current;
    skipRowAnim.current = false;
    const revealed = pendingExpand.current;
    pendingExpand.current = null;

    if (!isMotionEnabled()) return;
    if (revealed) {
      const box = groupBox(revealed);
      if (box) {
        Array.from(box.children)
          .slice(COLLAPSED_ROWS)
          .forEach((el) => replay(el as HTMLElement, "fadeRise .3s ease both"));
      }
      return;
    }
    if (skip) {
      // A collapse just committed: the surviving rows must not keep the fadeDrop
      // they were never given, nor a stale fadeRise from an earlier expand.
      rootRef.current
        ?.querySelectorAll<HTMLElement>("[data-group] > *")
        .forEach((el) => {
          el.style.animation = "";
        });
      return;
    }
    if (first) return;
    rootRef.current
      ?.querySelectorAll<HTMLElement>("[data-group] > *")
      .forEach((el) => replay(el, "fadeRise .3s ease both"));
    // Keyed on the expansion map alone — this is a post-commit DOM pass, not a
    // data effect. (`replay` and `groupBox` are re-created every render but only
    // read refs, so the lint rule is satisfied without a disable.)
  }, [expanded]);

  const profileNote =
    `${profileLabel} · local` + (modeNote ? ` · ${modeNote}` : "");

  return (
    <div
      ref={rootRef}
      className={
        "no-scrollbar flex h-full flex-col gap-[22px] overflow-y-auto pb-1.5 " +
        // The drawer is a floating panel over the page, so it needs the one
        // background this column otherwise never has — without it the scrim
        // shows straight through the slide-over.
        (isDrawer
          ? "w-full border-r border-line bg-paper px-4 pt-[calc(env(safe-area-inset-top)+20px)]"
          : "w-[212px] box-border pt-9")
      }
    >
      {isDrawer && onCloseDrawer && (
        <div className="flex shrink-0 justify-end pr-1">
          <button
            type="button"
            onClick={onCloseDrawer}
            aria-label="Close menu"
            className="flex h-11 w-11 items-center justify-center text-[13px] text-disabled transition-colors hover:text-ink"
          >
            «
          </button>
        </div>
      )}

      {/* Workspace — the two surfaces that are about this computer rather than
          about a conversation. Each hint is a real count (or the policy mode);
          never a placeholder. */}
      <div className="mt-1 shrink-0 border-l-2 border-rail pl-3.5">
        <div
          data-scramble="160"
          className="text-[11px] font-medium tracking-[.04em] text-faint"
        >
          Workspace
        </div>
        <div className="mt-[11px] flex flex-col gap-[9px] text-[12px]">
          <WorkspaceRow
            label="Tools"
            hint={toolsHint}
            active={view === "tools"}
            onClick={onOpenTools}
          />
          <WorkspaceRow
            label="Snapshots"
            hint={snapshotsHint}
            active={view === "snapshots"}
            onClick={onOpenSnapshots}
          />
        </div>
      </div>

      <button
        type="button"
        data-scramble="60"
        onClick={onNewChat}
        disabled={newChatDisabled}
        className="shrink-0 text-left text-[12px] text-accent transition-colors hover:text-ink disabled:cursor-not-allowed disabled:text-disabled disabled:hover:text-disabled max-md:min-h-[44px]"
      >
        ＋ New chat
      </button>

      {groups.map((group) => {
        const isExpanded = Boolean(expanded[group.key]);
        const rows = isExpanded ? group.ids : group.ids.slice(0, COLLAPSED_ROWS);
        const hidden = group.ids.length - rows.length;
        return (
          <div key={group.key} className="shrink-0 animate-[fadeRise_.3s_ease_both]">
            <button
              type="button"
              onClick={() => toggleGroup(group.key, group.ids.length, isExpanded)}
              className="flex w-full items-baseline justify-between pb-2.5 text-left"
            >
              <span className="text-[11px] font-medium tracking-[.04em] text-faint">
                {group.label}
              </span>
              <span className="font-mono text-[10px] text-disabled">
                {groupHint(group.ids.length, isExpanded)}
              </span>
            </button>
            <div
              data-group={group.key}
              className="flex flex-col gap-0.5 text-[12px]"
            >
              {rows.map((c) => (
                <ConversationRow
                  key={c.id}
                  conversation={c}
                  active={
                    view === "chat" &&
                    currentConversationId != null &&
                    c.id === currentConversationId
                  }
                  onOpen={onOpenConversation}
                  onRename={onRenameConversation}
                />
              ))}
              {hidden > 0 && (
                <button
                  type="button"
                  onClick={() => expandGroup(group.key)}
                  className="border-l-2 border-transparent py-1.5 pl-3 text-left text-[12px] text-disabled transition-colors hover:text-muted max-md:min-h-[44px]"
                >
                  {hidden} more…
                </button>
              )}
            </div>
          </div>
        );
      })}

      {/* Pinned to the bottom. The generous bottom padding lifts it clear of the
          composer row on the chat view (prototype: 69px). */}
      <div className="mt-auto flex shrink-0 flex-col gap-[3px] pb-[69px]">
        <button
          type="button"
          data-scramble="520"
          onClick={onOpenSettings}
          className={
            "border-l-2 py-1.5 pb-0.5 pl-3 text-left text-[12px] transition-colors hover:text-ink max-md:min-h-[44px] " +
            (view === "settings"
              ? "border-accent text-ink"
              : "border-transparent text-muted")
          }
        >
          Settings
        </button>
        <p className="pl-3.5 font-mono text-[10px] text-disabled">{profileNote}</p>
      </div>
    </div>
  );
}

// One Workspace entry: label left, machine fact right, both on one baseline.
function WorkspaceRow({
  label,
  hint,
  active,
  onClick,
}: {
  label: string;
  hint?: string;
  active: boolean;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={!onClick}
      className={
        "flex items-baseline justify-between text-left transition-colors hover:text-ink disabled:cursor-default max-md:min-h-[44px] " +
        (active ? "text-ink" : "text-muted")
      }
    >
      <span>{label}</span>
      {hint && <span className="ml-2 font-mono text-[10px] text-disabled">{hint}</span>}
    </button>
  );
}

// One conversation row. Single-click opens it; double-clicking the title swaps it
// for an inline editor (Enter/blur commits, Escape cancels) — the rename flow is
// unchanged, only its skin is. Active rows carry the 2px accent rail.
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
        className="block w-full border-l-2 border-accent bg-transparent py-1.5 pl-3 text-left text-[12px] text-ink caret-accent outline-none max-md:min-h-[44px]"
      />
    );
  }

  return (
    <button
      type="button"
      onClick={() => onOpen(c.id)}
      onDoubleClick={startEditing}
      title={c.title}
      // The accessible name is the title alone — the time beside it is a machine
      // fact, not part of what this row is called.
      aria-label={c.title}
      className={
        "flex w-full items-baseline justify-between gap-2 border-l-2 py-1.5 pl-3 text-left transition-colors hover:text-ink max-md:min-h-[44px] " +
        (active ? "border-accent text-ink" : "border-transparent text-muted")
      }
    >
      <span data-chat-title="1" className="min-w-0 flex-1 truncate">
        {c.title}
      </span>
      {formatRowTime(c.startedAt) && (
        <span className="shrink-0 font-mono text-[10px] text-disabled">
          {formatRowTime(c.startedAt)}
        </span>
      )}
    </button>
  );
}

// ---------------------------------------------------------------------------
// Pure helpers.
// ---------------------------------------------------------------------------
/** "collapse" while an expanded group is showing more than it has to; else a count. */
function groupHint(count: number, isExpanded: boolean): string {
  if (isExpanded && count > COLLAPSED_ROWS) return "collapse";
  return `${count} ${count === 1 ? "chat" : "chats"}`;
}

// Today vs. everything earlier, from each conversation's own `startedAt` (epoch
// SECONDS). A zero/absent value means we don't know when it started, so it goes
// to "Earlier" and shows no time at all — dating it "today" would be inventing a
// fact about the person's own history.
function bucketConversations(conversations: ConversationSummary[]): {
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

// HH:MM today · the weekday within the last week · a short date beyond that ·
// nothing at all when there is no usable timestamp.
export function formatRowTime(startedAt: number, now: Date = new Date()): string {
  if (!startedAt) return "";
  const d = new Date(startedAt * 1000);
  if (Number.isNaN(d.getTime())) return "";
  if (isSameDay(d, now)) {
    return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  }
  const days = (now.getTime() - d.getTime()) / 86_400_000;
  if (days >= 0 && days < 7) return d.toLocaleDateString(undefined, { weekday: "short" });
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function isSameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}
