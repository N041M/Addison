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
//
// A CONTINUED CHAT IS ONE THING (§4.8). When a long conversation is condensed,
// Addison carries on in a NEW conversation that records where it came from — so a
// chat the person experienced as continuous used to be two rows sharing a title
// with nothing saying one came from the other. `continuedFrom` is now read here:
// the newest part keeps the row, the older parts sit under it indented, and the
// group's count counts the chat once. NOTHING IS HIDDEN — every conversation is
// still drawn and still opens, which is the promise the whole feature rests on
// (the older transcript stays reachable, untouched).

import { useEffect, useRef, useState } from "react";
import type { ConversationSummary, View } from "../types/ui";
import { isMotionEnabled, scrambleElement } from "../lib/scramble";
import { formatRowTime, isSameDay } from "../lib/time";

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
  /**
   * Opens the Code review surface (Phase-3). ITS PRESENCE IS THE NAV GATE: App
   * passes a handler only under the Developer/Custom profile, and without one the
   * row is not rendered at all — not rendered disabled, not rendered dimmed,
   * absent. A Simple-profile window has no way to reach that screen from here,
   * which is the point; the core refuses every one of its calls independently.
   */
  onOpenCode?: () => void;
  /** Mono hint beside "Tools" — the trusted-folder count, else the policy mode. */
  toolsHint?: string;
  /** Mono hint beside "Restore points" — how many restore points exist. */
  snapshotsHint?: string;
  /**
   * Plain label for the active profile, e.g. "Simple profile" — or UNDEFINED
   * while the engine has not answered yet, in which case the footer note is not
   * rendered at all. Silence is correct here; a default asserted as fact is not.
   */
  profileLabel?: string;
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
  onOpenCode,
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

  // Entries, not rows: one continued chat is one entry, however many
  // conversations it is made of. That is what the collapse count and the group
  // hint are counting from here on.
  const buckets = bucketConversations(lineageEntries(conversations));
  const groups = ([["today", "Today"], ["earlier", "Earlier"]] as const)
    .map(([key, label]) => ({ key, label, entries: buckets[key] }))
    .filter((g) => g.entries.length > 0);

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

  // Nothing is claimed until the profile is known — see `profileLabel` above.
  const profileNote = profileLabel
    ? `${profileLabel} · local` + (modeNote ? ` · ${modeNote}` : "")
    : null;

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
            label="Restore points"
            hint={snapshotsHint}
            active={view === "snapshots"}
            onClick={onOpenSnapshots}
          />
          {/* Developer/Custom only — see the prop's comment. Rendered ONLY when
              App hands over a handler, so there is no row to reason about in
              Simple and no disabled control inviting a question about why. */}
          {onOpenCode && (
            <WorkspaceRow label="Code" active={view === "code"} onClick={onOpenCode} />
          )}
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
        const rows = isExpanded ? group.entries : group.entries.slice(0, COLLAPSED_ROWS);
        const hidden = group.entries.length - rows.length;
        return (
          <div key={group.key} className="shrink-0 animate-[fadeRise_.3s_ease_both]">
            <button
              type="button"
              onClick={() => toggleGroup(group.key, group.entries.length, isExpanded)}
              className="flex w-full items-baseline justify-between pb-2.5 text-left"
            >
              <span className="text-[11px] font-medium tracking-[.04em] text-faint">
                {group.label}
              </span>
              <span className="font-mono text-[10px] text-disabled">
                {groupHint(group.entries.length, isExpanded)}
              </span>
            </button>
            <div
              data-group={group.key}
              className="flex flex-col gap-0.5 text-[12px]"
            >
              {rows.map((entry) => (
                // One entry = one chat. The wrapper is what the expand/collapse
                // animation counts and animates (it walks `[data-group] > *`), so
                // a continued chat rises and drops as the single thing it is.
                <div key={entry.conversation.id} className="flex flex-col gap-0.5">
                  <ConversationRow
                    conversation={entry.conversation}
                    active={
                      view === "chat" &&
                      currentConversationId != null &&
                      entry.conversation.id === currentConversationId
                    }
                    onOpen={onOpenConversation}
                    onRename={onRenameConversation}
                  />
                  {entry.earlier.map((older) => (
                    <ConversationRow
                      key={older.id}
                      conversation={older}
                      earlier
                      active={
                        view === "chat" &&
                        currentConversationId != null &&
                        older.id === currentConversationId
                      }
                      onOpen={onOpenConversation}
                      onRename={onRenameConversation}
                    />
                  ))}
                </div>
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

      {/* Pinned to the bottom. 72.5px, not the prototype's 69: with the
          stacked composer (text row over the controls strip, 12px of air above
          the strip), this puts "Settings" level with "Write to Addison…" —
          measured in the browser (owner request 2026-07-26; retuned twice as
          the strip landed and gained its breathing room). */}
      <div className="mt-auto flex shrink-0 flex-col gap-[3px] pb-[72.5px]">
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
        {/* Empty until the profile is known: the line keeps its height (so the
            footer does not jump when the answer lands) but says nothing, and is
            hidden from assistive tech while it holds only the spacer. */}
        <p
          className="pl-3.5 font-mono text-[10px] text-disabled"
          data-testid="profile-note"
          aria-hidden={profileNote === null || undefined}
        >
          {profileNote ?? "\u00a0"}
        </p>
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
  earlier = false,
  onOpen,
  onRename,
}: {
  conversation: ConversationSummary;
  active: boolean;
  /**
   * This row is an earlier part of the chat above it. Indented one step, and its
   * mono fact says what it is instead of when it started — "earlier" is the thing
   * worth knowing about a row whose whole meaning is that it came first. It opens
   * and renames exactly like any other row: the older transcript is reachable, in
   * full, which is what the continuation feature promises.
   */
  earlier?: boolean;
  onOpen: (id: string) => void;
  onRename: (id: string, title: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(c.title);
  const inputRef = useRef<HTMLInputElement>(null);
  const titleRef = useRef<HTMLSpanElement>(null);
  const wasActive = useRef(active);

  useEffect(() => {
    if (editing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing]);

  // Clicking a chat scrambles its title as it becomes the active row — the
  // prototype's activeId-change behaviour (its `[data-chat-title]` pass). Only
  // on the transition: re-renders of an already-active row stay still, and the
  // first paint belongs to App's initial pass. scrambleElement itself no-ops
  // under reduced motion.
  useEffect(() => {
    if (active && !wasActive.current && titleRef.current) {
      scrambleElement(titleRef.current, 0);
    }
    wasActive.current = active;
  }, [active]);

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
        className={
          "block w-full border-l-2 border-accent bg-transparent py-1.5 text-left text-[12px] text-ink caret-accent outline-none max-md:min-h-[44px] " +
          (earlier ? "pl-6" : "pl-3")
        }
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
      // fact, not part of what this row is called. An earlier part says so in its
      // name, because the indent that says it visually says nothing out loud.
      aria-label={earlier ? `${c.title} — earlier part of this chat` : c.title}
      className={
        "flex w-full items-baseline justify-between gap-2 border-l-2 py-1.5 text-left transition-colors hover:text-ink max-md:min-h-[44px] " +
        (earlier ? "pl-6 " : "pl-3 ") +
        (active ? "border-accent text-ink" : "border-transparent text-muted")
      }
    >
      <span ref={titleRef} data-chat-title="1" className="min-w-0 flex-1 truncate">
        {c.title}
      </span>
      {earlier ? (
        <span className="shrink-0 font-mono text-[10px] text-disabled">earlier</span>
      ) : (
        formatRowTime(c.startedAt) && (
          <span className="shrink-0 font-mono text-[10px] text-disabled">
            {formatRowTime(c.startedAt)}
          </span>
        )
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

/** One chat as the sidebar draws it: its newest part, and the older parts it
 * carried on from (newest first). `earlier` is empty for an ordinary chat. */
export interface ConversationEntry {
  conversation: ConversationSummary;
  earlier: ConversationSummary[];
}

/**
 * Fold `continuedFrom` into entries: a conversation that another listed row
 * continues from stops being a row of its own and becomes an earlier part of it.
 *
 * Exported because it is the whole of the grouping rule and it is worth testing
 * on its own. Three properties it holds deliberately:
 *
 *   * **Every conversation appears exactly once.** A row claimed as an earlier
 *     part is claimed by ONE chain (`taken`), and the last loop puts back anything
 *     no chain reached — a lineage that somehow points in a circle would otherwise
 *     make both of its chats vanish from history, which is the one outcome this
 *     list may never produce.
 *   * **A lineage pointing outside the list is ignored.** The id has to belong to
 *     a conversation that is actually here.
 *   * **Order is the order it was given** (the core sends newest first), and a
 *     chain is walked oldest-ward, so an entry reads newest → older → older still.
 */
export function lineageEntries(conversations: ConversationSummary[]): ConversationEntry[] {
  const byId = new Map(conversations.map((c) => [c.id, c]));
  // Ids that some other listed row continues from: never a row of their own.
  const isEarlierPart = new Set<string>();
  for (const c of conversations) {
    if (c.continuedFrom && byId.has(c.continuedFrom)) isEarlierPart.add(c.continuedFrom);
  }
  const taken = new Set<string>();
  const entries: ConversationEntry[] = [];
  for (const c of conversations) {
    if (isEarlierPart.has(c.id)) continue;
    taken.add(c.id);
    const earlier: ConversationSummary[] = [];
    let parentId = c.continuedFrom;
    while (parentId && !taken.has(parentId)) {
      const parent = byId.get(parentId);
      if (!parent) break;
      taken.add(parent.id);
      earlier.push(parent);
      parentId = parent.continuedFrom;
    }
    entries.push({ conversation: c, earlier });
  }
  // Nothing may disappear: anything no chain reached is its own entry, in place.
  for (const c of conversations) {
    if (!taken.has(c.id)) entries.push({ conversation: c, earlier: [] });
  }
  return entries;
}

// Today vs. everything earlier, from each chat's own `startedAt` (epoch SECONDS).
// A zero/absent value means we don't know when it started, so it goes to "Earlier"
// and shows no time at all — dating it "today" would be inventing a fact about the
// person's own history. A continued chat is bucketed by its NEWEST part, because
// that is when the person last used the chat this entry stands for; each older
// part keeps its own row under it either way.
function bucketConversations(entries: ConversationEntry[]): {
  today: ConversationEntry[];
  earlier: ConversationEntry[];
} {
  const now = new Date();
  const today: ConversationEntry[] = [];
  const earlier: ConversationEntry[] = [];
  for (const entry of entries) {
    const startedAt = entry.conversation.startedAt;
    if (startedAt && isSameDay(new Date(startedAt * 1000), now)) today.push(entry);
    else earlier.push(entry);
  }
  return { today, earlier };
}

// The row time and the day comparison behind it live in `lib/time.ts` — the Code
// screen's Changes list renders the same fact in the same shape, and one format
// with two homes would become two formats.
