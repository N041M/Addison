// The chat column — the empty state and the message thread (DARK direction;
// docs/design-brief-dark, "Screens → Chat empty state / Thread").
//
// EMPTY: a centred greeting stack — the time-of-day greeting (scrambling in),
// one subline, and three suggestion chips that fill the composer. The
// prototype's faint dotted "starfield" behind it was REMOVED (owner decision
// 2026-07-26): five 1px dots over a 464x276 box never read as a field, and two
// of them landed within 20px of the type — one level with the subline — where a
// lone speck beside a word reads as a dead pixel rather than as decoration. It REPLACES the seeded "welcome" message: an empty
// chat is an invitation, not a message Addison already sent. The first-run block
// (FirstRunBanner) rides in the same stack when a launch starts unconfigured.
//
// THREAD: one centred 580px column, 32px between turns, behind the vertical fade
// mask with the scrollbar hidden. A turn is an 11px label ("You" `disabled` /
// "Addison" `accent` — owner decision 2026-07-26, a deliberate deviation from
// the prototype's `ink` label) over a 15.5px/1.65 body — `ink-soft` for what you wrote,
// `ink` for what Addison wrote. Assistant answers keep their markdown/mermaid
// rendering; a 7×14px blinking block rides after the text while a reply streams.
//
// TWO THINGS THAT ARE DELIBERATE, not stylistic:
//
//   * An arriving answer FORMATS ITSELF AS IT LANDS (owner request 2026-08-21,
//     reworked 2026-08-22), and the rule that makes that safe is one cut: the
//     last newline behind the scramble's resolved edge — the prefix the frame
//     and the true text still agree on. Everything before it is parsed fresh
//     every frame; the line still being written stays plain pre-wrap text. The
//     streaming scramble puts random glyphs in that tail, and feeding those to a
//     markdown parser 26 times a second would make a stray `#` a heading for one
//     frame and reflow the answer under the reader's eyes — so the parser only
//     ever sees true text ending at a line break, and a glyph can never become
//     structure. `StreamingMarkdown` renders it; `lib/streamMarkdown.ts` owns
//     where the cut is made and why every frame agrees with the final document.
//     The reveal of a settled answer runs through the same component, so it
//     formats behind the resolved edge too, rather than arriving whole on the
//     frame the reveal lands.
//   * Switching conversations staggers the rows in and re-scrambles them, but a
//     body is only scrambled when it is a single text node. A rendered markdown
//     body has element children, and the engine's leaf guard would skip it
//     anyway — this check makes that refusal explicit rather than incidental, so
//     a rendered answer is never rewritten by an animation.

import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
  type UIEvent,
} from "react";
import type { DisplayMessage, ThreadContinuation } from "../types/ui";
import { Markdown } from "./Markdown";
import { StreamingMarkdown } from "./StreamingMarkdown";
import { isMotionEnabled, scrambleElement } from "../lib/scramble";
import { AddisonMark } from "./AddisonMark";
import { SelectionAsk } from "./SelectionAsk";

interface Props {
  messages: DisplayMessage[];
  onRetry: () => void;
  /** Whether the last answer can be regenerated (a real turn has happened). */
  retryAvailable: boolean;
  onRewindTo: (messageId: string) => void;
  /**
   * Developer profile only: when a turn fails and the core supplied raw error
   * text, show it in a collapsed "Technical details" block under the plain
   * message. Off (and absent) for Simple, so its thread is byte-identical.
   */
  showTechnicalDetails?: boolean;
  /**
   * The scrambled DISPLAY text for the message still streaming (useTurn). The
   * message's own `content` stays the true text — this only decorates it.
   */
  streamDisplay?: string | null;
  /**
   * WHICH message `streamDisplay` decorates. Keyed by id, not by the `pending`
   * flag, because a finished answer is revealed AFTER it settles.
   */
  streamMessageId?: string | null;
  /** Which conversation is on screen; a change staggers + re-scrambles the rows. */
  conversationKey?: string | null;
  /**
   * Fills the composer, once, without sending anything: an empty-state
   * suggestion chip, and the selection popover's Ask / Explain (SelectionAsk).
   */
  onSuggestion?: (text: string) => void;
  /** Rendered with the empty stack, and above the messages once there are any. */
  header?: ReactNode;
  /**
   * Set when this chat continues an earlier one (§4.8) — the boundary marker at
   * the top of the thread. It is drawn from the STORED conversation row, so it is
   * still there on the tenth reopening; the one sentence said when the condensing
   * happened rode the per-turn Activity channel and was gone by the next send.
   */
  continuation?: ThreadContinuation | null;
  /** Rendered after the last message, inside the scroll (consent + work inline). */
  footer?: ReactNode;
  /**
   * A key naming footer content the reader MUST be shown — today, the pending
   * consent card. `null` when there is none; a different string means a different
   * question. When it turns non-null the thread scrolls to the foot, once.
   *
   * A key rather than the footer node itself, and that is the whole design: App
   * rebuilds `footer` on every render, so the follow effect below deliberately
   * leaves it out of its dependencies — which is exactly why a card appended to it
   * used to arrive BELOW THE FOLD of a container whose scrollbar is hidden, with
   * nothing to say it was there (KNOWN-BUGS P2 #3: "Working…" for four minutes and
   * a card revealed only by Stop — Stop changes `messages`, which is what finally
   * scrolled). This is the narrow signal that says "this particular footer change
   * is worth moving the reader for", so the rest of the footer still cannot.
   */
  attention?: string | null;
}

const SENDER_LABEL: Record<string, string> = {
  user: "You",
  assistant: "Addison",
};

/** The three chips, verbatim from the prototype. They only fill the composer. */
const SUGGESTIONS = ["Tidy my Downloads folder", "Draft an email", "Plan the weekend"];

/** Per-row stagger when a conversation is opened (prototype: 70ms / 40ms). */
const ROW_STAGGER_MS = 70;
const TEXT_STAGGER_MS = 40;

// Which conversation the stagger last played for. MODULE scope, not a ref:
// ChatThread unmounts whenever a surface replaces the chat column, so an
// instance ref forgets across the round trip — opening a chat FROM Settings
// remounted a fresh component whose mount-skip swallowed the change, and the
// switch played no stagger and no scramble (user report, 2026-07-26).
// `undefined` means "no thread has painted yet this session" — that first
// paint belongs to App's initial scramble pass. Only one ChatThread ever
// renders at a time, so module scope cannot cross wires.
let lastStaggeredConversation: string | null | undefined = undefined;

// How far each conversation's window had been opened when the reader last left
// it. Module scope for the same reason as the stagger key above: ChatThread
// unmounts whenever a surface replaces the chat column, so an instance ref
// forgets across a Settings detour — and coming back to a thread you had opened
// out, to find only the last 30 messages again, is the window taking something
// away that the old full-list render never did.
const rememberedShown = new Map<string, number>();

/** Test-only: forget the session's thread state (stagger history, windows). */
export function resetThreadStaggerForTests() {
  lastStaggeredConversation = undefined;
  rememberedShown.clear();
}

/** How many trailing rows animate on a switch — the ones the bottom-scrolled
 * viewport can actually show. Everything above the fold stays still. */
const STAGGER_ROWS = 12;

// How much of a thread is RENDERED when it opens. `loadConversation` still hands
// over every message, but the thread opens scrolled to the bottom, so parsing
// markdown (and drawing mermaid) for hundreds of rows above the fold buys the
// reader nothing and costs the whole open. MUST stay >= STAGGER_ROWS: the switch
// stagger animates the last STAGGER_ROWS children, and windowing is a cost cut
// that may not quietly shrink the motion language on its way through.
const INITIAL_WINDOW_ROWS = 30;
/** How many further rows appear each time the reader reaches the top. */
const EXTEND_BATCH_ROWS = 30;
/** How close to the top counts as "reaching it", in pixels of scroll offset. */
const EXTEND_THRESHOLD_PX = 300;

/** How close to the foot of the thread still counts as "following along" —
 * enough slack for a fractional scroll position, not enough to swallow a
 * deliberate scroll away. */
const NEAR_BOTTOM_PX = 40;

export function ChatThread({
  messages,
  onRetry,
  retryAvailable,
  onRewindTo,
  showTechnicalDetails = false,
  streamDisplay,
  streamMessageId,
  conversationKey,
  onSuggestion,
  header,
  continuation = null,
  footer,
  attention = null,
}: Props) {
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);

  // The thread shows the human turns; live tool steps live in the widget rail /
  // work block, so tool messages aren't repeated here.
  const visible = messages.filter((m) => m.role !== "tool");
  const isEmpty = visible.length === 0;

  // "Is this the same thread as a moment ago?", cheaply: the count plus the two
  // end ids. Used only by the stagger effect below, to tell adopting the launch
  // conversation's id (same messages, new key) from opening another chat.
  const threadIdentity =
    `${visible.length}:${visible[0]?.id ?? ""}:${visible[visible.length - 1]?.id ?? ""}`;
  const previousIdentity = useRef(threadIdentity);

  // Whether the reader is parked at the foot of the thread. Recorded when THEY
  // scroll, not measured inside the effect below: by the time an effect runs the
  // new text is already in the DOM, where "was at the bottom" and "was one
  // answer above it" measure the same.
  const followingRef = useRef(true);

  // --- The window ----------------------------------------------------------
  // Held as a count of rows hidden at the TOP, keyed by the conversation it was
  // measured against. A count from the top rather than a `slice(-n)`: a message
  // appended while a reply streams must lengthen the window at the BOTTOM, and a
  // trailing slice would answer that by dropping the topmost rendered row —
  // pulling text out from under a reader who has scrolled up mid-stream.
  const [thread, setThread] = useState<{ key: string | null | undefined; hiddenCount: number }>({
    key: undefined,
    hiddenCount: 0,
  });
  // Scroll geometry captured just before an extension, consumed by the layout
  // effect below. `null` means "no extension in flight".
  const pendingExtendRef = useRef<{ prevHeight: number; prevTop: number } | null>(null);
  // Whether an ARRIVAL at the top may release a batch — see handleScroll.
  const armedRef = useRef(true);
  // How many rows the reader has ASKED to see: raised only when they extend or
  // reveal, never from the size the DOM happens to have reached. Those two
  // diverge as soon as a thread grows on its own, and treating the grown size as
  // the floor would re-render all of it on the next rewind.
  const shownFloorRef = useRef(INITIAL_WINDOW_ROWS);
  // The newest message the PERSON sent, and the list's length, at the last
  // commit — together they tell "they just sent something" from "the model is
  // still writing" and from "the list just got shorter".
  const lastUserIdRef = useRef<string | null>(null);
  const lengthRef = useRef(0);

  // Re-measure DURING render, not in an effect (React's documented "adjusting
  // state when a prop changes" pattern — the component re-runs before its
  // children render, so the pass that saw the old count never reaches the DOM).
  // An effect would commit one render of the FULL list and only then correct
  // itself, which is precisely the cost this windowing exists to remove.
  //
  // The second branch is load-bearing rather than defensive: App's
  // `handleRewindTo` TRUNCATES `messages` (`prev.slice(0, idx)`) under the SAME
  // key, so a rewind anchored on the topmost rendered row would leave
  // `visible.length === hiddenCount` and render no messages at all — a blank
  // column, since `isEmpty` reads the full list and the greeting never appears
  // either. The narrowed count is written BACK rather than clamped for the one
  // render that needs it: a stale count left in state is a ceiling the window
  // climbs back to as the thread grows again, re-hiding rows already on screen.
  // Its floor is how much the reader ASKED to see, so a rewind cannot collapse
  // an opened-out window at the moment they are editing their own history.
  const windowKey = conversationKey ?? null;
  const switchingWindow = thread.key !== windowKey;
  const maxHidden = Math.max(
    0,
    visible.length - Math.max(INITIAL_WINDOW_ROWS, shownFloorRef.current),
  );
  if (switchingWindow) {
    // A stale adjustment must not fire against the new list: its geometry
    // describes the thread that just left the screen.
    pendingExtendRef.current = null;
    armedRef.current = true; // the new thread opens at the bottom...
    followingRef.current = true; // ...and following it is what that means
    const remembered =
      (windowKey != null ? rememberedShown.get(windowKey) : undefined) ?? INITIAL_WINDOW_ROWS;
    shownFloorRef.current = remembered;
    setThread({
      key: windowKey,
      hiddenCount: Math.max(0, visible.length - Math.max(INITIAL_WINDOW_ROWS, remembered)),
    });
  } else if (thread.hiddenCount > maxHidden) {
    setThread({ key: windowKey, hiddenCount: maxHidden });
  }

  const hiddenCount = thread.hiddenCount;
  const windowed = hiddenCount > 0 ? visible.slice(hiddenCount) : visible;

  // Remembering the SHOWN count, not the hidden one: the two agree only while
  // the thread's length does not change, and restoring a stale hidden count
  // after a rewind hands back fewer rows than the reader had opened out.
  // Written after commit, so a discarded render-phase pass cannot poison it.
  useEffect(() => {
    if (windowKey != null) {
      rememberedShown.set(windowKey, Math.max(INITIAL_WINDOW_ROWS, shownFloorRef.current));
    }
  });

  // Reaching the top reveals the next batch — on ARRIVAL, once. Being near the
  // top is a state a gesture can sit in for many events (an elastic overscroll
  // pinned at 0), and each of those events would otherwise release another
  // batch: one flick would render most of a long thread, which is the cost this
  // mechanism exists to avoid. Re-arming needs the reader to leave the zone,
  // which the restore below normally does on its own by pushing them down past
  // the rows it just added.
  const handleScroll = (event: UIEvent<HTMLDivElement>) => {
    const el = event.currentTarget;
    followingRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX;
    if (el.scrollTop > EXTEND_THRESHOLD_PX) {
      armedRef.current = true;
      return;
    }
    if (!armedRef.current || pendingExtendRef.current || hiddenCount === 0) return;
    armedRef.current = false;
    // Suppressed for THIS prepend only: the browser's own scroll anchoring would
    // compensate for the added rows on top of the layout effect's adjustment,
    // and two corrections for one prepend make the thread jump. Leaving it off
    // permanently would be a different bug — anchoring is what keeps the reader
    // still when content above them grows late, which this thread does every
    // time a diagram or an image finishes rendering.
    el.style.overflowAnchor = "none";
    pendingExtendRef.current = { prevHeight: el.scrollHeight, prevTop: el.scrollTop };
    shownFloorRef.current = visible.length - Math.max(0, hiddenCount - EXTEND_BATCH_ROWS);
    setThread((t) => ({ ...t, hiddenCount: Math.max(0, t.hiddenCount - EXTEND_BATCH_ROWS) }));
  };

  // The control above the window. Scrolling pages a batch at a time, which is
  // right for reading back — but someone who is SEARCHING wants the whole
  // conversation present, because the browser's own find-in-page (and select
  // all, and copy) only ever see what is rendered. So this reveals everything
  // above in one action, at a cost the person asked for explicitly, and the
  // count in the label is what tells them how big that ask is.
  function handleShowEarlier() {
    const el = listRef.current;
    if (el) {
      el.style.overflowAnchor = "none";
      pendingExtendRef.current = { prevHeight: el.scrollHeight, prevTop: el.scrollTop };
    }
    shownFloorRef.current = visible.length;
    setThread((t) => ({ ...t, hiddenCount: 0 }));
  }

  // Rows prepended above the viewport push everything down by exactly the height
  // they add; without this the reader is thrown backwards through the thread by
  // that much. Before paint, so the jump is never seen.
  useLayoutEffect(() => {
    const el = listRef.current;
    if (!el) return;
    const pending = pendingExtendRef.current;
    pendingExtendRef.current = null;
    el.style.overflowAnchor = ""; // the prepend is over, whether it landed or was dropped
    if (!pending) return;
    el.scrollTop = pending.prevTop + (el.scrollHeight - pending.prevHeight);
  }, [hiddenCount]);

  // A window that does not overflow its container can never be extended: no
  // overflow means no scroll event, and the rows above it would be unreachable.
  // Rare at 30 rows, but it is the failure mode anyone lowering
  // INITIAL_WINDOW_ROWS would walk into, and the check costs two reads. A
  // container with no measurable height is not evidence of anything — jsdom, or
  // a hidden column — so it is left alone.
  useLayoutEffect(() => {
    const el = listRef.current;
    if (!el || hiddenCount === 0 || !el.clientHeight) return;
    if (el.scrollHeight > el.clientHeight) return;
    setThread((t) => ({ ...t, hiddenCount: Math.max(0, t.hiddenCount - EXTEND_BATCH_ROWS) }));
  }, [hiddenCount, messages]);

  // Retry is a deliberate ask for a new answer, and `runTurn` replaces the
  // trailing assistant row rather than appending a user one — so the send force
  // in the follow effect cannot see it, and it needs saying here.
  function handleRetry() {
    followingRef.current = true;
    onRetry();
  }

  // Keep the newest content in view — but only for a reader who is already
  // there. Skipped while the thread is empty: the greeting stack is the content
  // then, and it must stay put.
  //
  // TWO THINGS THIS EFFECT MUST NOT DO, both measured on this branch:
  //   * Jail the reader. A reveal repaints ~30 times in 1.1s, and scrolling up
  //     to reread an earlier answer was impossible — the thread yanked itself
  //     back down before the wheel stopped.
  //   * Scroll on renders that added nothing. `header` and `footer` are
  //     deliberately absent from the dependency list: App rebuilds the footer as
  //     a fresh inline fragment on EVERY render, so listing it meant an
  //     unrelated state change elsewhere in the app scrolled the thread.
  //     `streamMessageId` is an id, not content, and is out for the same reason.
  //
  // The exception is a message the PERSON just sent: they acted, and the reply
  // belongs at the bottom of the thread they were looking at. It has to be the
  // newest USER message, not the newest message — `runTurn` appends the user's
  // row and the pending assistant row in ONE update (useTurn.ts), so the last
  // element of the list is ALWAYS the assistant and a check on it never fires on
  // the one path that produces it. The length guard is the other half: an id can
  // also become "newest" because the rows after it were removed, and a rewind is
  // not a send.
  useEffect(() => {
    if (isEmpty) return;
    let newestUserId: string | null = null;
    for (let i = visible.length - 1; i >= 0; i--) {
      if (visible[i].role === "user") {
        newestUserId = visible[i].id;
        break;
      }
    }
    const sent = newestUserId !== lastUserIdRef.current && visible.length > lengthRef.current;
    lastUserIdRef.current = newestUserId;
    lengthRef.current = visible.length;
    if (sent) followingRef.current = true;
    if (!followingRef.current) return;
    bottomRef.current?.scrollIntoView({ block: "end" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, streamDisplay]);

  // A question the reader has to answer arrived in the footer. Bring them to it.
  //
  // This is the ONE footer change allowed to move the thread, and it is allowed for
  // the same reason a message the person just sent is: the turn is now blocked on
  // them. It fires on ARRIVAL only — a card whose key is unchanged never re-scrolls,
  // so someone reading back through the thread with a card already on screen is left
  // alone — and it forces `followingRef` true, because a consent card that cannot be
  // seen is a safety failure and being scrolled up is not consent to miss it.
  // Seeded null rather than with the first `attention`: a thread MOUNTING with a
  // card already pending (coming back to chat from a surface) is an arrival too.
  const lastAttentionRef = useRef<string | null>(null);
  useEffect(() => {
    const key = attention ?? null;
    const arrived = key !== null && key !== lastAttentionRef.current;
    lastAttentionRef.current = key;
    if (!arrived) return;
    followingRef.current = true;
    // THE CARD, when it can be found, and the foot of the thread otherwise. The
    // difference is not cosmetic: with the widget rail open inline, the card rides
    // at the TOP of a rail whose widget list follows it, so scrolling to the foot
    // can leave the question above the viewport instead of below it. The handle is
    // the one PermissionCard already carries for exactly this kind of structural
    // claim (`data-consent-card`), scoped to this thread's own subtree.
    const card = listRef.current?.querySelector("[data-consent-card]") ?? null;
    (card ?? bottomRef.current)?.scrollIntoView({ block: "end" });
  }, [attention]);

  // Opening another conversation: the rows rise in one after another and their
  // labels/bodies resolve out of the scramble behind them. The session's very
  // first paint is skipped — App's initial pass owns it (module-scope key above,
  // so a remount can't swallow a real switch).
  //
  // Only the LAST `STAGGER_ROWS` rows animate: the thread opens scrolled to the
  // bottom, so those are the ones on screen — and a real conversation can hold
  // hundreds of turns, where animating every row means a forced layout and a
  // 38ms-tick scramble timer PER ROW. That work was a measurable part of "some
  // chats take longer to open than others" (user report, 2026-07-26); rows
  // above the fold get nothing, which is also what the eye sees either way.
  // One reflow for the whole batch, not one per row.
  useEffect(() => {
    const key = conversationKey ?? null;
    if (lastStaggeredConversation === undefined) {
      lastStaggeredConversation = key;
      return;
    }
    if (lastStaggeredConversation === key) return;
    // Learning the launch conversation's id is NOT a switch. `null` means "the
    // conversation the core minted for this launch, whose id we don't know yet",
    // and the first completed turn adopts it — the key goes null → "c-…" with
    // the very same messages on screen. Replaying the switch there re-staggered
    // and re-scrambled the thread on top of the reveal that was still running
    // (review 2026-07-26). Opening a different chat from an untouched one also
    // goes null → "c-…", but it BRINGS different messages, which is what the
    // identity check separates.
    const adopted =
      lastStaggeredConversation === null && previousIdentity.current === threadIdentity;
    lastStaggeredConversation = key;
    const list = listRef.current;
    if (adopted || !list || !isMotionEnabled()) return;
    const rows = Array.from(list.children).slice(-STAGGER_ROWS) as HTMLElement[];
    rows.forEach((el) => {
      el.style.animation = "none";
    });
    list.getBoundingClientRect(); // one reflow so every restart is seen
    const cancels: Array<() => void> = [];
    rows.forEach((el, i) => {
      el.style.animation = `fadeRise .38s ease both ${i * ROW_STAGGER_MS}ms`;
      el.querySelectorAll("[data-msg-text], [data-scramble-live]").forEach((text, j) => {
        // Leaf text only: a rendered markdown body has element children and is
        // left alone (see the file header).
        if (text.children.length === 0) {
          cancels.push(scrambleElement(text, i * ROW_STAGGER_MS + j * TEXT_STAGGER_MS));
        }
      });
    });
    // Two dozen 38ms intervals can be in flight after a switch and nothing else
    // ever stopped them: unmounting the thread (opening Settings, closing the
    // app) left them ticking over detached nodes for as long as the webview
    // lived, and a second switch stacked another set on top.
    return () => cancels.forEach((cancel) => cancel());
    // The stagger plays on a conversation CHANGE only. `threadIdentity` is read
    // as a snapshot of the render that changed the key — it must never be a
    // trigger, or every new message would replay the switch animation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conversationKey]);

  // Feeds the adoption check above with the PREVIOUS render's identity —
  // declared after that effect so it is still the previous value when it runs.
  useEffect(() => {
    previousIdentity.current = threadIdentity;
  });

  const lastAssistantId = [...visible]
    .reverse()
    .find((m) => m.role === "assistant" && !m.pending)?.id;

  if (isEmpty) {
    return (
      <div className="no-scrollbar fade-mask-y flex min-h-0 w-full max-w-[580px] flex-1 flex-col overflow-y-auto">
        <EmptyState header={header} onSuggestion={onSuggestion} />
        {footer && <div className="shrink-0 pb-6">{footer}</div>}
        {/* The scroll anchor lives here too. A consent card can arrive before the
            first answer does — the greeting stack is tall, and without this the
            attention effect above had nothing to scroll to on the one screen where
            the card is most likely to be pushed out of sight. */}
        <div ref={bottomRef} />
      </div>
    );
  }

  return (
    <>
      <div
        ref={listRef}
        onScroll={handleScroll}
        className="no-scrollbar fade-mask-y flex min-h-0 w-full max-w-[580px] flex-1 flex-col gap-8 overflow-y-auto pb-6 pt-9"
      >
        {header}

        {/* Above everything: the boundary is where this chat begins, and the
            "Show N earlier messages" control below it is about THIS chat's own
            messages, which all come after it. */}
        {continuation && <ContinuationMarker continuation={continuation} />}

        {hiddenCount > 0 && (
          // Without this the thread simply starts 30 messages ago, and the fade
          // mask at the top of the column makes that cut read as the beginning of
          // the conversation. Same idiom as "Retry this answer" — a plain accent
          // action inside the thread, not a new piece of furniture.
          <button
            type="button"
            onClick={handleShowEarlier}
            className="shrink-0 self-center text-[12px] text-accent transition-colors hover:text-ink max-md:min-h-[44px]"
          >
            {hiddenCount === 1 ? "Show 1 earlier message" : `Show ${hiddenCount} earlier messages`}
          </button>
        )}

        {windowed.map((m) => (
          <MessageRow
            key={m.id}
            message={m}
            revealing={m.id === streamMessageId && streamDisplay != null}
            display={m.id === streamMessageId && streamDisplay != null ? streamDisplay : m.content}
            canRewind={m.role === "user" && Boolean(m.storeId)}
            canRetry={m.id === lastAssistantId && retryAvailable}
            onRewindTo={onRewindTo}
            onRetry={handleRetry}
            showTechnicalDetails={showTechnicalDetails}
          />
        ))}

        {footer}

        <div ref={bottomRef} />
      </div>

      {/* Highlight → Ask / Explain. It renders nothing until a selection lands
          inside ONE settled message body, and it only ever writes into the
          composer — the same one-shot seed the suggestion chips use. A SIBLING of
          the scroll container, not a child: the switch stagger animates that
          container's last twelve children, and a panel that is not a turn must not
          take one of those slots. */}
      {onSuggestion && (
        <SelectionAsk
          containerRef={listRef}
          onSeed={onSuggestion}
          conversationKey={conversationKey}
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// The empty state: greeting, subline, chips.
// ---------------------------------------------------------------------------
function EmptyState({
  header,
  onSuggestion,
}: {
  header?: ReactNode;
  onSuggestion?: (text: string) => void;
}) {
  const greetingRef = useRef<HTMLDivElement>(null);

  // The greeting resolves out of the scramble whenever this stack appears — on
  // launch, and again on every new chat. (`data-greeting` also puts it in App's
  // initial load pass; the engine refuses to run twice on one element, so the
  // two paths can never fight over the same text node.)
  useEffect(() => {
    scrambleElement(greetingRef.current, 0);
  }, []);

  return (
    // `m-auto`, not `flex-1 justify-center`: centring a flex CHILD that is
    // taller than its scroll container puts the overflow above the scroll
    // origin, where it can never be reached. With auto margins the stack sits in
    // the middle while there is room and scrolls normally once there isn't — so
    // the first-run block's actions stay reachable on a short window.
    <div className="m-auto flex w-full shrink-0 flex-col items-center gap-[14px] py-9">
      {/* First-run only: the 44px mark above the greeting — the brandbook's
          "SPLASH · FIRST RUN" application. `header` is only ever the first-run
          block, so its presence IS the first-run signal. */}
      {header && (
        <div className="animate-[fadeRise_.6s_ease_both]">
          <AddisonMark size={44} />
        </div>
      )}
      <h1
        ref={greetingRef}
        data-greeting=""
        className="m-0 text-[26px] font-normal tracking-display text-ink"
      >
        {greeting()}
      </h1>
      <p className="m-0 animate-[fadeRise_.6s_ease_both_.6s] text-[14px] text-muted">
        Ask anything, or hand me a chore. Everything can be undone.
      </p>
      {/* gap-x only: each chip already carries a 44px touch height below md, so
          a vertical gap on top of it would open a canyon between wrapped rows. */}
      <div className="mt-3 flex animate-[fadeRise_.6s_ease_both_.9s] flex-wrap justify-center gap-x-[22px] gap-y-0">
        {SUGGESTIONS.map((text) => (
          <button
            key={text}
            type="button"
            onClick={() => onSuggestion?.(text)}
            className="shrink-0 whitespace-nowrap text-[12px] text-accent transition-colors hover:text-ink max-md:min-h-[44px]"
          >
            {text}
          </button>
        ))}
      </div>

      {header && <div className="mt-6 w-full max-w-[420px]">{header}</div>}
    </div>
  );
}

// Time-of-day greeting (prototype: `greeting`). Before 5 the day hasn't started.
function greeting(): string {
  const h = new Date().getHours();
  if (h < 5) return "Still up?";
  if (h < 12) return "Good morning.";
  if (h < 18) return "Good afternoon.";
  return "Good evening.";
}

// ---------------------------------------------------------------------------
// The boundary marker (§4.8, item 4) — the top of a chat that continues another.
//
// DURABLE BY CONSTRUCTION, which is the entire reason it exists: it renders the
// `conversations` row (`continued_from_conversation_id` + `summary`), so it is on
// screen every time the chat is opened. The one sentence said when the condensing
// happened goes out on the Activity Panel note channel, which is cleared at the
// start of every turn and never persisted — the note tells you as it happens, this
// says the same thing for as long as the chat exists. Both are true; only one of
// them is still there tomorrow.
//
// No new furniture: the 2px rule + 11px label is the annotation idiom the
// free-model line already uses, and the disclosure is the "Technical details"
// idiom with plain-language copy. No accent — nothing here is an action or a live
// state, it is a fact about the chat.
// ---------------------------------------------------------------------------
function ContinuationMarker({ continuation }: { continuation: ThreadContinuation }) {
  return (
    <div
      data-testid="continuation-marker"
      className="shrink-0 animate-[fadeRise_.4s_ease_both] border-l-2 border-rail pl-2.5"
    >
      <p className="m-0 text-[11px] font-medium tracking-[.04em] text-faint">
        Continued from an earlier chat
      </p>
      <p className="m-0 mt-1.5 text-[12px] leading-[1.6] text-muted">
        This chat was getting long, so Addison condensed the earlier part into a summary and
        carried on here. Nothing was deleted: the earlier chat is still in your history, with
        every message it had.
      </p>
      {/* Only when there is one. A continuation whose summary did not survive still
          says the boundary is here rather than saying nothing at all. */}
      {continuation.summary && (
        <details className="mt-2">
          <summary className="cursor-pointer font-mono text-[10px] text-disabled transition-colors hover:text-muted">
            The summary Addison carried over
          </summary>
          <p className="m-0 mt-1.5 whitespace-pre-wrap text-[12px] leading-[1.6] text-ink-soft">
            {continuation.summary}
          </p>
        </details>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// One turn.
// ---------------------------------------------------------------------------
interface RowProps {
  message: DisplayMessage;
  /** What to render — the true content, or the streaming overlay over it. */
  display: string;
  /** This message's text is resolving out of the scramble right now. */
  revealing: boolean;
  canRewind: boolean;
  canRetry: boolean;
  onRewindTo: (messageId: string) => void;
  onRetry: () => void;
  showTechnicalDetails: boolean;
}

function MessageRow({
  message,
  display,
  revealing,
  canRewind,
  canRetry,
  onRewindTo,
  onRetry,
  showTechnicalDetails,
}: RowProps) {
  const label = SENDER_LABEL[message.role] ?? message.role;
  const isAddison = message.role === "assistant";
  const showRaw = showTechnicalDetails && message.failed && Boolean(message.raw);
  // The free-model disclaimer (Phase-2 step 3, contract D5). Renders whenever a
  // KNOWN-FREE model answered — owner decision 2026-08-12. It used to also require
  // `routed` (a free answer the user did not choose), and that is why the QA pass
  // of 9 August saw no note at all: the ordinary way to get a free answer is to
  // PICK the local model, and picking it set routed=false and hid the disclosure.
  // The claim is about the answer's cost, not about who chose it. `free` still
  // comes from the core and is free-by-construction only (a local Ollama model);
  // the frontend never derives it. Not an error, so no danger tone.
  const showFreeChip = message.answeredWith?.free === true;
  // The settled answer, rendered whole: nothing is arriving and nothing is
  // resolving, so there is no edge to hold anything back.
  const asMarkdown = isAddison && !message.failed && !message.pending && !revealing;
  // Nothing has arrived yet. The blinking block alone would be a shrug; the
  // honest sentence stays, and the block rides after it (pending copy is kept
  // word for word from the Fern build).
  const showWriting = message.pending && display.length === 0;
  // Text is still arriving, or still resolving out of the scramble. It formats
  // block by block behind that edge — see the file header, and note that a
  // FAILED turn is excluded: an error is one plain sentence that must read at
  // once, not something to animate structure into.
  const asStream = isAddison && !message.failed && (message.pending || revealing) && !showWriting;
  // Whether this body may be quoted into the composer (SelectionAsk). SETTLED
  // TEXT ONLY: a row that is still arriving, or still resolving out of the
  // scramble, has random glyphs in its tail, and "Addison is writing…" is not a
  // sentence anybody wants to ask about. Read from the row's state, never from
  // the characters on screen.
  const askSelectable = !message.pending && !revealing;

  return (
    <div className="group shrink-0 animate-[fadeRise_.4s_ease_both]">
      <div className="flex items-baseline justify-between gap-3">
        <span
          data-scramble-live="140"
          className={
            "text-[11px] font-medium tracking-[.04em] " +
            // Addison's sender label is ACCENT, not the prototype's `ink` —
            // owner decision 2026-07-26 ("change the colour of the addison
            // name to purple"), one deliberate deviation from the handoff.
            (isAddison ? "text-accent" : "text-disabled")
          }
        >
          {label}
        </span>
        {canRewind && (
          <button
            type="button"
            onClick={() => message.storeId && onRewindTo(message.storeId)}
            className="font-mono text-[10px] text-disabled opacity-0 transition-opacity hover:text-ink focus:opacity-100 group-hover:opacity-100 max-md:opacity-100"
          >
            Rewind to here
          </button>
        )}
      </div>

      {asMarkdown ? (
        <div
          data-ask-selectable={askSelectable ? "" : undefined}
          className="mt-2 text-[15.5px] leading-[1.65] text-ink"
        >
          <Markdown content={message.content} pending={false} />
        </div>
      ) : asStream ? (
        <div className="mt-2 text-[15.5px] leading-[1.65] text-ink">
          {/* `display` is the scramble's frame while one is over this message,
              and the true content otherwise (motion off, or between reveals). */}
          <StreamingMarkdown content={message.content} display={display} showCursor />
        </div>
      ) : (
        <p
          data-ask-selectable={askSelectable ? "" : undefined}
          className={
            "m-0 mt-2 whitespace-pre-wrap text-[15.5px] leading-[1.65] " +
            (message.failed
              ? "text-danger"
              : showWriting
                ? "text-muted"
                : isAddison
                  ? "text-ink"
                  : "text-ink-soft")
          }
        >
          <span data-msg-text="1">{showWriting ? "Addison is writing…" : display}</span>
          {(message.pending || revealing) && (
            // The block cursor riding the streamed tail (7×14px, blinking).
            <span
              aria-hidden="true"
              className="ml-[3px] inline-block h-[14px] w-[7px] animate-[blink_1.1s_step-start_infinite] bg-ink align-[-1px]"
            />
          )}
        </p>
      )}

      {showFreeChip && (
        // A live annotation, not an error: the 2px rule + small label idiom the
        // rest of the surface uses. The DOM text is the frozen sentence verbatim.
        <p className="m-0 mt-2.5 border-l-2 border-rail pl-2.5 text-[11px] font-medium tracking-[.04em] text-faint">
          Answered with a free model.
        </p>
      )}

      {showRaw && (
        <details className="mt-2.5">
          <summary className="cursor-pointer font-mono text-[10px] text-disabled transition-colors hover:text-muted">
            Technical details
          </summary>
          <pre className="mt-1.5 overflow-x-auto whitespace-pre-wrap rounded-[5px] border border-line bg-panel px-3 py-2 font-mono text-[10.5px] text-ink-soft">
            {message.raw}
          </pre>
        </details>
      )}

      {canRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-2.5 text-[12px] text-accent transition-colors hover:text-ink max-md:min-h-[44px]"
        >
          Retry this answer
        </button>
      )}
    </div>
  );
}
