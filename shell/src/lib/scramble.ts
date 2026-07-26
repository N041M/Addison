// The character-scramble — Addison's signature motion (docs/design-brief-dark,
// "Interactions & Behavior"; prototype.html `scrambleEl` ~line 412).
//
// Text resolves out of random glyphs: each character is given a resolve time
// spread across ~620–800ms (25% jitter, occasionally right-to-left), and every
// still-unresolved character re-randomises on a 38ms tick from ONE of three
// pools. Whitespace never scrambles, so word shapes hold still while the letters
// settle.
//
// Implemented ONCE here and used app-wide: the view title, the sidebar labels
// and chat rows, surface titles and rows, the greeting, and any leaf element
// carrying a `data-scramble*` attribute (the global click handler).
//
// THREE PROPERTIES THIS FILE OWES THE REST OF THE APP, in order of importance:
//
//   1. It never corrupts text. The animation writes into a single text node and
//      the completion path restores the EXACT original string, character for
//      character — a motion flourish that can leave garbage in a sentence
//      Addison wrote is not a flourish, it's a bug with a nice easing curve.
//   2. It is off when motion is off. `prefers-reduced-motion: reduce` and the
//      module `motionEnabled` flag are both hard no-ops at every entry point,
//      checked BEFORE any timer is scheduled. (styles.css disables CSS animation
//      under reduced motion; that CSS rule cannot stop a JS loop rewriting the
//      DOM, which is why the check is repeated here.)
//   3. It never runs twice on one element. A second trigger while a scramble is
//      in flight is ignored rather than queued — two loops writing the same text
//      node is exactly how (1) would be broken.

import { useEffect, useRef, type RefObject } from "react";

// The three glyph pools. One is picked per element, per run — mixing them within
// a single word reads as noise rather than as text resolving.
const POOLS = [
  "ABCDEFGHIKLMNOPRSTUVXYZ0234689",
  "abcdefghikmnoprstuvxyz<>/",
  "#%&*+=-·:;<>/",
];

/** Re-randomise cadence for unresolved characters. */
const TICK_MS = 38;
/** Total resolve window: 620ms + up to 180ms, i.e. 620–800ms. */
const SPREAD_BASE_MS = 620;
const SPREAD_RANGE_MS = 180;
/** Share of the window used for the left-to-right sweep; the rest is jitter. */
const SWEEP_SHARE = 0.75;
const JITTER_SHARE = 0.25;
/** How often the sweep runs right-to-left instead. */
const REVERSE_CHANCE = 0.15;

/** Delay applied to a click-triggered scramble (prototype: `scrambleEl(el, 40)`). */
const CLICK_DELAY_MS = 40;

/** The elements the initial-load pass scrambles (staggered). */
export const INITIAL_SCRAMBLE_SELECTOR =
  "[data-scramble], [data-scramble-live], [data-greeting]";

/** The elements the global click handler will scramble when clicked. */
const CLICK_SCRAMBLE_SELECTOR =
  "[data-scramble],[data-scramble-live],[data-scramble-click]";

// --- The motion flag --------------------------------------------------------
// A single module-level switch, mirroring the prototype's `motion` prop. It is
// deliberately NOT React state: the engine is called from effects, event
// handlers and plain DOM code, and a flag that lived in a context would be
// unreadable from half of them.
let motionEnabled = true;

/** Turn the whole scramble language off (or back on) app-wide. */
export function setMotionEnabled(next: boolean): void {
  motionEnabled = next;
}

/** True when the OS asks for reduced motion. Safe when matchMedia is absent. */
export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return false;
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

/**
 * Whether animation may run at all: the module flag AND the OS preference. Every
 * entry point in this file checks it, and the app's own CSS animations
 * (fadeRise/fadeDrop staggers) check it too before they are applied inline.
 */
export function isMotionEnabled(): boolean {
  return motionEnabled && !prefersReducedMotion();
}

// Elements with a scramble in flight. A WeakSet rather than an expando property
// so nothing is attached to the DOM node and entries disappear with the node.
const running = new WeakSet<Element>();

function randomFrom(pool: string): string {
  return pool[(Math.random() * pool.length) | 0];
}

function nowMs(): number {
  return typeof performance !== "undefined" && typeof performance.now === "function"
    ? performance.now()
    : Date.now();
}

/**
 * Scramble one element's text, starting after `delayMs`.
 *
 * The element must hold exactly one text node (a "leaf") — anything else is left
 * alone rather than flattened, because rewriting an element's children would
 * destroy whatever markup a caller put inside it.
 *
 * Returns a cancel function. Cancelling STOPS the loop and leaves the DOM as it
 * is; it deliberately does not restore the original string, because the usual
 * reason to cancel is that React has just written newer text into the same node
 * (see `useScrambleOnChange`) and putting the old string back would show stale
 * content. A run that ends on its own always restores the exact original.
 */
export function scrambleElement(el: Element | null | undefined, delayMs = 0): () => void {
  if (!el || !isMotionEnabled()) return () => {};
  if (running.has(el)) return () => {};
  running.add(el);

  let interval: ReturnType<typeof setInterval> | null = null;
  let cancelled = false;

  const stop = () => {
    cancelled = true;
    if (interval !== null) clearInterval(interval);
    interval = null;
    running.delete(el);
  };

  const timeout = setTimeout(() => {
    if (cancelled) return;
    const node = el.firstChild;
    // Single text node only (prototype's guard, kept exactly).
    if (!node || node.nodeType !== 3 || el.childNodes.length !== 1) {
      running.delete(el);
      return;
    }
    const orig = node.nodeValue ?? "";
    if (!orig.trim()) {
      running.delete(el);
      return;
    }

    const pool = POOLS[(Math.random() * POOLS.length) | 0];
    const n = orig.length;
    const total = SPREAD_BASE_MS + Math.random() * SPREAD_RANGE_MS;

    // Per-character resolve times: a sweep across the window plus jitter, in
    // reading order or (sometimes) backwards.
    const order = [...Array(n).keys()];
    if (Math.random() < REVERSE_CHANCE) order.reverse();
    const resolveAt = new Array<number>(n);
    order.forEach((charIndex, k) => {
      resolveAt[charIndex] =
        (k / Math.max(1, n - 1)) * total * SWEEP_SHARE + Math.random() * total * JITTER_SHARE;
    });

    const start = nowMs();
    // The last frame THIS loop wrote. React can rewrite the same text node
    // mid-run — renaming a conversation while its sidebar row is scrambling is
    // the real case — and from that moment every frame here is built from a
    // string that is no longer true, ending with the completion path restoring
    // it permanently: the old name comes back and stays. So the loop abandons a
    // node that no longer holds its own last frame instead of finishing over it.
    let written = orig;
    interval = setInterval(() => {
      if (node.nodeValue !== written) {
        stop();
        return;
      }
      const elapsed = nowMs() - start;
      let out = "";
      let done = true;
      for (let i = 0; i < n; i++) {
        const c = orig[i];
        if (/\s/.test(c)) {
          out += c; // whitespace passes through untouched
          continue;
        }
        if (elapsed >= resolveAt[i]) out += c;
        else {
          done = false;
          out += randomFrom(pool);
        }
      }
      node.nodeValue = out;
      written = out;
      if (done) {
        // The one line that matters: the exact original string, restored.
        node.nodeValue = orig;
        stop();
      }
    }, TICK_MS);
  }, delayMs);

  return () => {
    clearTimeout(timeout);
    stop();
  };
}

// --- The streaming variant --------------------------------------------------
// The same language applied to text that is still ARRIVING (design-brief-dark,
// "Motion → Streaming reply"; prototype.html `send` ~line 475). A ~14-character
// scrambled window trails the received tail and resolves left→right at ~5
// characters per 38ms tick, so an answer settles as it lands instead of
// materialising a paragraph at a time — speeding up when it has fallen behind
// text that has already arrived, because a display that is minutes behind the
// answer is not an animation, it is a stall.
//
// The property that matters here is the same one `scrambleElement` owes: the
// display can lag the truth, but it may never INVENT it. So the window's leading
// edge is clamped to the text actually received — the animation is never a
// character ahead of the model — and `finish()` lands on the exact string.
//
// This engine does NOT touch the DOM. It hands frames to a callback, because the
// message it decorates is React state: the true text stays in the store and only
// the *display* is scrambled (useTurn keeps them apart, and a test pins it).

/** Width of the scrambled window trailing the resolved text. */
const STREAM_WINDOW_CHARS = 14;
/** How far the window's leading edge advances per tick. */
export const STREAM_ADVANCE_CHARS = 5;

/**
 * Roughly how long a whole-answer REVEAL should take, end to end.
 *
 * The core does not stream token by token: it emits the finished answer as a
 * single `conversation.streamChunk`, so the text arrives complete and would
 * otherwise appear in one frame. Revealing it with the same scramble language
 * is the prototype's own behaviour — its canned reply is likewise whole, and it
 * animates it — but the prototype's fixed 5-chars-per-tick only reads well at
 * its demo length: a real 3,000-character answer would take ~23 seconds, which
 * is not an animation, it is a wait. So the rate ADAPTS to keep the reveal
 * bounded, and never drops below the prototype's tempo for short answers.
 */
const REVEAL_TARGET_MS = 1100;

/** Chars-per-tick for revealing a finished answer of `length` characters. */
export function revealAdvanceFor(length: number): number {
  const ticks = Math.max(1, Math.round(REVEAL_TARGET_MS / TICK_MS));
  return Math.max(STREAM_ADVANCE_CHARS, Math.ceil(length / ticks));
}

interface StreamScrambleOptions {
  /**
   * Chars per tick, for text whose full length is already known (a reveal).
   * Omitted — the streaming case, where more is still arriving — the rate
   * ADAPTS to the backlog: the prototype's tempo while the display is keeping
   * up, faster while it is behind. A non-finite value is ignored rather than
   * honoured (see the clamp in `createStreamScramble`).
   */
  advanceChars?: number;
  /** Called once, after the frame that lands on the exact received text. */
  onDone?: () => void;
}

export interface StreamScramble {
  /**
   * The true text received SO FAR — the whole prefix, not a delta. Passing the
   * full string each time is what keeps the engine incapable of drifting from
   * the message it is decorating.
   */
  push(received: string): void;
  /** Tear down without emitting anything further. */
  stop(): void;
}

/**
 * Create a streaming scramble. `onFrame` receives the text to DISPLAY.
 *
 * With motion off (module flag or `prefers-reduced-motion`) this is a hard
 * no-op: no timer is ever scheduled and every `push` emits the received text
 * unchanged, so the reply simply appends — the same fallback the rest of the
 * file makes.
 */
export function createStreamScramble(
  onFrame: (display: string) => void,
  options: StreamScrambleOptions = {},
): StreamScramble {
  // A fixed rate is honoured only when it is a usable number. A NaN or Infinity
  // advance leaves `front` non-finite, so `resolved >= n` is never true: the
  // interval spins for the life of the app, and — because useTurn only clears
  // its overlay when the reveal reports done — the message would stay scrambled
  // and un-markdowned forever. Anything unusable falls through to the adaptive
  // rate, which is always finite.
  const fixedAdvance =
    typeof options.advanceChars === "number" && Number.isFinite(options.advanceChars)
      ? Math.max(1, Math.floor(options.advanceChars))
      : null;
  const pool = POOLS[(Math.random() * POOLS.length) | 0];
  let received = "";
  // The window's leading edge, in characters. Everything before
  // `front - STREAM_WINDOW_CHARS` is resolved; the span between is scrambled.
  let front = 0;
  let interval: ReturnType<typeof setInterval> | null = null;
  // The display has reached the end of what has arrived (the engine is idle).
  let caughtUp = false;
  // The streaming (no fixed rate) catch-up rate, in chars per tick.
  let adaptiveAdvance = STREAM_ADVANCE_CHARS;
  // `onDone` is documented "called once" and consumers TEAR DOWN on it — useTurn
  // drops the overlay and releases the engine — so a second call after a later
  // push would dismantle a reveal it never started.
  let doneFired = false;

  function stop(): void {
    if (interval !== null) clearInterval(interval);
    interval = null;
  }

  function tick(): void {
    const n = received.length;
    // How much has arrived but is not yet resolved on screen.
    const behind = n - Math.max(0, Math.min(n, front - STREAM_WINDOW_CHARS));
    // No fixed rate means text is still arriving. Hold the prototype's tempo
    // while the display keeps up, but never let it fall further than one reveal
    // window behind text the app is ALREADY holding: at a flat 5 chars/tick a
    // 50,000-character answer takes six minutes to show, and the reader is left
    // looking at 390 characters of an answer that has completely arrived. The
    // rate only ever ratchets UP until the display catches up — recomputing it
    // from the shrinking backlog every tick decays it geometrically, which
    // stretches one burst across several windows instead of the one it is
    // sized for.
    if (fixedAdvance === null) {
      adaptiveAdvance = Math.max(adaptiveAdvance, revealAdvanceFor(behind));
    }
    const advance = fixedAdvance ?? adaptiveAdvance;
    // Clamp to `n + window`: at that point the resolved edge has reached the end
    // of what has arrived, so the leading edge can never run ahead of the text
    // and then "resolve" characters the model has not sent.
    front = Math.min(front + advance, n + STREAM_WINDOW_CHARS);
    const resolved = Math.max(0, Math.min(n, front - STREAM_WINDOW_CHARS));
    let out = received.slice(0, resolved);
    for (let i = resolved; i < Math.min(n, front); i++) {
      const c = received[i];
      // Whitespace passes through, exactly as in the one-shot engine: word and
      // line shapes hold still while the letters settle.
      out += /\s/.test(c) ? c : randomFrom(pool);
    }
    onFrame(out);
    // Caught up with everything received: `out` IS the received string, exactly.
    // Idle rather than spin — the next `push` restarts the loop where it left
    // off, so a stream that pauses mid-answer resumes instead of jumping.
    // `onDone` fires here, AFTER that exact frame, so a reveal can hand the
    // message back to its normal (markdown) rendering without the caller having
    // to guess when the animation ended by comparing strings.
    if (resolved >= n) {
      caughtUp = true;
      adaptiveAdvance = STREAM_ADVANCE_CHARS;
      stop();
      if (!doneFired) {
        doneFired = true;
        options.onDone?.();
      }
    }
  }

  return {
    push(next: string): void {
      received = next;
      if (!isMotionEnabled()) {
        onFrame(received);
        return;
      }
      if (interval !== null) return; // already running; it will pick `next` up
      // The FIRST frame is emitted SYNCHRONOUSLY, before any timer exists. The
      // caller sets "this message is being revealed" and receives this frame in
      // one React batch; scheduling the first frame 38ms out instead meant the
      // settled answer was committed with no overlay over it — so it rendered
      // once in full, as formatted markdown, and dissolved into glyphs a frame
      // later (review 2026-07-26).
      caughtUp = false;
      tick();
      // `tick` may already have landed on the whole string (a short answer at a
      // high rate); starting an interval then would restart a resolved engine.
      if (!caughtUp) interval = setInterval(tick, TICK_MS);
    },
    stop,
  };
}

/**
 * The staggered initial-load pass. Each matched element's own
 * `data-scramble` / `data-scramble-live` value is its base delay, plus a 40ms
 * step that cycles every five elements so a whole screen doesn't resolve in
 * lockstep.
 */
export function scrambleAll(selector: string, root: ParentNode = document): void {
  if (!isMotionEnabled()) return;
  root.querySelectorAll(selector).forEach((el, idx) => {
    const base =
      Number(el.getAttribute("data-scramble")) ||
      Number(el.getAttribute("data-scramble-live")) ||
      0;
    scrambleElement(el, base + (idx % 5) * 40);
  });
}

/**
 * Scramble whenever the rendered text changes.
 *
 * Mount is deliberately skipped — the initial-load pass (`scrambleAll`) owns the
 * first paint, and scrambling here too would run two loops over one node. The
 * previous run is cancelled before a new one starts, so a title that changes
 * twice quickly resolves to the LAST value rather than an earlier one.
 */
export function useScrambleOnChange(
  ref: RefObject<HTMLElement | null>,
  value: string,
  delayMs = 0,
): void {
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    const cancel = scrambleElement(ref.current, delayMs);
    return cancel;
    // `ref` is a stable ref object; re-running on it would be meaningless.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, delayMs]);
}

/**
 * Install the document-level click handler: clicking any LEAF element carrying
 * `data-scramble`, `data-scramble-live` or `data-scramble-click` scrambles it.
 * Leaf means no element children — a click on a container would otherwise
 * scramble a wrapper whose text node isn't the one the person pointed at.
 *
 * Returns the uninstaller (call it from an effect's cleanup).
 */
export function installScrambleClickHandler(target: Document = document): () => void {
  const onClick = (event: Event) => {
    const node = event.target;
    if (!(node instanceof Element)) return;
    const leaf = node.closest(CLICK_SCRAMBLE_SELECTOR);
    if (!leaf || leaf.children.length !== 0) return;
    scrambleElement(leaf, CLICK_DELAY_MS);
  };
  target.addEventListener("click", onClick);
  return () => target.removeEventListener("click", onClick);
}
