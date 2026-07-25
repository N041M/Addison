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
export const CLICK_DELAY_MS = 40;

/** The elements the initial-load pass scrambles (staggered). */
export const INITIAL_SCRAMBLE_SELECTOR =
  "[data-scramble], [data-scramble-live], [data-greeting]";

/** The elements the global click handler will scramble when clicked. */
export const CLICK_SCRAMBLE_SELECTOR =
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
    interval = setInterval(() => {
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
