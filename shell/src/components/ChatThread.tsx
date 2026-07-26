// The chat column — the empty state and the message thread (DARK direction;
// docs/design-brief-dark, "Screens → Chat empty state / Thread").
//
// EMPTY: a centred greeting stack over a faint dotted starfield — the
// time-of-day greeting (scrambling in), one subline, and three suggestion chips
// that fill the composer. It REPLACES the seeded "welcome" message: an empty
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
//   * While a message is `pending` its body renders as PLAIN pre-wrap text, and
//     it becomes markdown the moment the turn settles. The streaming scramble
//     puts random glyphs in the tail; feeding those to a markdown parser 26
//     times a second would make a stray `#` a heading for one frame and reflow
//     the answer under the reader's eyes.
//   * Switching conversations staggers the rows in and re-scrambles them, but a
//     body is only scrambled when it is a single text node. A rendered markdown
//     body has element children, and the engine's leaf guard would skip it
//     anyway — this check makes that refusal explicit rather than incidental, so
//     a rendered answer is never rewritten by an animation.

import { useEffect, useRef, type ReactNode } from "react";
import type { DisplayMessage } from "../types/ui";
import { Markdown } from "./Markdown";
import { isMotionEnabled, scrambleElement } from "../lib/scramble";
import { AddisonMark } from "./AddisonMark";

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
  /** Fills the composer from an empty-state suggestion chip. */
  onSuggestion?: (text: string) => void;
  /** Rendered with the empty stack, and above the messages once there are any. */
  header?: ReactNode;
  /** Rendered after the last message, inside the scroll (consent + work inline). */
  footer?: ReactNode;
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

/** Test-only: forget the session's stagger history between test cases. */
export function resetThreadStaggerForTests() {
  lastStaggeredConversation = undefined;
}

/** How many trailing rows animate on a switch — the ones the bottom-scrolled
 * viewport can actually show. Everything above the fold stays still. */
const STAGGER_ROWS = 12;

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
  footer,
}: Props) {
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);

  // The thread shows the human turns; live tool steps live in the widget rail /
  // work block, so tool messages aren't repeated here.
  const visible = messages.filter((m) => m.role !== "tool");
  const isEmpty = visible.length === 0;

  // Keep the newest content in view. Skipped while the thread is empty: the
  // greeting stack is the content then, and it must stay put.
  useEffect(() => {
    if (isEmpty) return;
    bottomRef.current?.scrollIntoView({ block: "end" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, streamDisplay, streamMessageId, header, footer]);

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
    lastStaggeredConversation = key;
    const list = listRef.current;
    if (!list || !isMotionEnabled()) return;
    const rows = Array.from(list.children).slice(-STAGGER_ROWS) as HTMLElement[];
    rows.forEach((el) => {
      el.style.animation = "none";
    });
    list.getBoundingClientRect(); // one reflow so every restart is seen
    rows.forEach((el, i) => {
      el.style.animation = `fadeRise .38s ease both ${i * ROW_STAGGER_MS}ms`;
      el.querySelectorAll("[data-msg-text], [data-scramble-live]").forEach((text, j) => {
        // Leaf text only: a rendered markdown body has element children and is
        // left alone (see the file header).
        if (text.children.length === 0) {
          scrambleElement(text, i * ROW_STAGGER_MS + j * TEXT_STAGGER_MS);
        }
      });
    });
  }, [conversationKey]);

  const lastAssistantId = [...visible]
    .reverse()
    .find((m) => m.role === "assistant" && !m.pending)?.id;

  if (isEmpty) {
    return (
      <div className="no-scrollbar fade-mask-y flex min-h-0 w-full max-w-[580px] flex-1 flex-col overflow-y-auto">
        <EmptyState header={header} onSuggestion={onSuggestion} />
        {footer && <div className="shrink-0 pb-6">{footer}</div>}
      </div>
    );
  }

  return (
    <div
      ref={listRef}
      className="no-scrollbar fade-mask-y flex min-h-0 w-full max-w-[580px] flex-1 flex-col gap-8 overflow-y-auto pb-6 pt-9"
    >
      {header}

      {visible.map((m) => (
        <MessageRow
          key={m.id}
          message={m}
          revealing={m.id === streamMessageId && streamDisplay != null}
          display={m.id === streamMessageId && streamDisplay != null ? streamDisplay : m.content}
          canRewind={m.role === "user" && Boolean(m.storeId)}
          canRetry={m.id === lastAssistantId && retryAvailable}
          onRewindTo={onRewindTo}
          onRetry={onRetry}
          showTechnicalDetails={showTechnicalDetails}
        />
      ))}

      {footer}

      <div ref={bottomRef} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// The empty state: greeting, subline, chips — and the starfield behind them.
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
    <div className="relative m-auto flex w-full shrink-0 flex-col items-center gap-[14px] py-9">
      {/* The starfield — a few 1px dots, one of them accent. Decoration only:
          it never intercepts a click. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-[10%] inset-y-[15%]"
        style={{
          backgroundImage: [
            "radial-gradient(1px 1px at 18% 30%, rgb(var(--c-ink) / .3) 50%, transparent 51%)",
            "radial-gradient(1px 1px at 72% 18%, rgb(var(--c-ink) / .22) 50%, transparent 51%)",
            "radial-gradient(1px 1px at 88% 62%, rgb(var(--c-ink) / .18) 50%, transparent 51%)",
            "radial-gradient(1px 1px at 34% 78%, rgb(var(--c-ink) / .2) 50%, transparent 51%)",
            "radial-gradient(1.5px 1.5px at 55% 45%, rgb(var(--c-accent) / .25) 50%, transparent 51%)",
          ].join(", "),
        }}
      />

      {/* First-run only: the 44px mark above the greeting — the brandbook's
          "SPLASH · FIRST RUN" application. `header` is only ever the first-run
          block, so its presence IS the first-run signal. */}
      {header && (
        <div className="relative animate-[fadeRise_.6s_ease_both]">
          <AddisonMark size={44} />
        </div>
      )}
      <h1
        ref={greetingRef}
        data-greeting=""
        className="relative m-0 text-[26px] font-normal tracking-display text-ink"
      >
        {greeting()}
      </h1>
      <p className="relative m-0 animate-[fadeRise_.6s_ease_both_.6s] text-[14px] text-muted">
        Ask anything, or hand me a chore. Everything can be undone.
      </p>
      {/* gap-x only: each chip already carries a 44px touch height below md, so
          a vertical gap on top of it would open a canyon between wrapped rows. */}
      <div className="relative mt-3 flex animate-[fadeRise_.6s_ease_both_.9s] flex-wrap justify-center gap-x-[22px] gap-y-0">
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

      {header && <div className="relative mt-6 w-full max-w-[420px]">{header}</div>}
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
  // The free-model disclaimer (Phase-2 step 3, contract D5 [S-b]). Renders ONLY
  // when a free model answered a turn the user did not choose it for — both
  // booleans come from the core; the frontend never re-derives `routed`. Not an
  // error, so no danger tone: it's Addison telling you which model replied.
  const showFreeChip = Boolean(message.answeredWith?.free && message.answeredWith?.routed);
  // Markdown once the turn has settled AND its text has finished resolving.
  // Feeding scrambled glyphs to the markdown parser 26×/s makes a stray `#` a
  // heading for one frame, so a revealing answer stays plain pre-wrap text and
  // swaps to markdown on the last frame (see the file header).
  const asMarkdown = isAddison && !message.failed && !message.pending && !revealing;
  // Nothing has arrived yet. The blinking block alone would be a shrug; the
  // honest sentence stays, and the block rides after it (pending copy is kept
  // word for word from the Fern build).
  const showWriting = message.pending && display.length === 0;

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
        <div className="mt-2 text-[15.5px] leading-[1.65] text-ink">
          <Markdown content={message.content} pending={false} />
        </div>
      ) : (
        <p
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
