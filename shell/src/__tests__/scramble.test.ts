// The scramble engine (lib/scramble.ts) — Addison's signature motion.
//
// Three properties are worth a test, and they are the three the file's header
// promises, because each one fails silently in a way a person would read as a
// bug in Addison rather than as an animation:
//
//   (a) it always lands on the EXACT original string. The animation writes
//       random glyphs into a live text node; if the completion path drifted by
//       one character, Addison's own sentences would come out subtly wrong and
//       nothing would report an error.
//   (b) whitespace passes through untouched — word shapes hold still while the
//       letters settle. (Also the cheapest proof that the intermediate frames
//       really are scrambled, not just the final one.)
//   (c) reduced motion is a hard no-op. styles.css already disables CSS
//       animation for those users, but no CSS rule can stop a JS interval
//       rewriting the DOM 26 times a second — so the engine has to check for
//       itself, and this is the test that says it does.
//
// Timers are faked including `performance`, because the engine measures elapsed
// time with performance.now() (as the prototype does); without it in `toFake`
// the loop would advance but never believe any time had passed.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  scrambleElement,
  setMotionEnabled,
  isMotionEnabled,
  prefersReducedMotion,
} from "../lib/scramble";

const FAKE = [
  "setTimeout",
  "clearTimeout",
  "setInterval",
  "clearInterval",
  "Date",
  "performance",
] as const;

// A leaf element: exactly one text node, which is all the engine will touch.
function leaf(text: string): HTMLElement {
  const el = document.createElement("span");
  el.textContent = text;
  document.body.appendChild(el);
  return el;
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: [...FAKE] });
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  setMotionEnabled(true);
  document.body.innerHTML = "";
  // Some tests install a matchMedia stub; jsdom ships without one.
  delete (window as unknown as { matchMedia?: unknown }).matchMedia;
});

describe("scrambleElement", () => {
  it("resolves to the exact original text, and never changes its length", () => {
    const TEXT = "Good afternoon. Everything can be undone.";
    const el = leaf(TEXT);

    scrambleElement(el, 0);

    // Mid-flight: still animating, same length, and not yet the final string.
    vi.advanceTimersByTime(76);
    expect(el.textContent).toHaveLength(TEXT.length);
    expect(el.textContent).not.toBe(TEXT);

    // The window is 620–800ms; well past it the original is back, byte for byte.
    vi.advanceTimersByTime(2000);
    expect(el.textContent).toBe(TEXT);
  });

  it("honours a start delay before touching the text", () => {
    const TEXT = "Snapshots";
    const el = leaf(TEXT);

    scrambleElement(el, 300);
    vi.advanceTimersByTime(250);
    expect(el.textContent).toBe(TEXT); // hasn't started yet

    vi.advanceTimersByTime(2000);
    expect(el.textContent).toBe(TEXT); // and finishes clean
  });

  it("never scrambles whitespace", () => {
    // Pin Math.random so every unresolved character renders the same glyph:
    // pool index 1, no reverse, and a resolve window whose earliest character
    // lands well after the first few ticks. What is left in the frame is
    // therefore exactly "one filler glyph per non-space character".
    vi.spyOn(Math, "random").mockReturnValue(0.5);
    const TEXT = "Tidy my Downloads folder\nand say so";
    const el = leaf(TEXT);

    scrambleElement(el, 0);

    for (const step of [38, 38, 38]) {
      vi.advanceTimersByTime(step);
      const frame = el.textContent ?? "";
      expect(frame).toHaveLength(TEXT.length);
      for (let i = 0; i < TEXT.length; i++) {
        if (/\s/.test(TEXT[i])) expect(frame[i]).toBe(TEXT[i]);
      }
      // …and the non-whitespace really is scrambled at this point.
      expect(frame).not.toBe(TEXT);
    }

    vi.advanceTimersByTime(2000);
    expect(el.textContent).toBe(TEXT);
  });

  it("leaves the text alone entirely under prefers-reduced-motion", () => {
    window.matchMedia = ((query: string) => ({
      matches: query.includes("prefers-reduced-motion"),
      media: query,
      addEventListener() {},
      removeEventListener() {},
    })) as unknown as typeof window.matchMedia;

    expect(prefersReducedMotion()).toBe(true);
    expect(isMotionEnabled()).toBe(false);

    const TEXT = "Restore points";
    const el = leaf(TEXT);
    scrambleElement(el, 0);

    vi.advanceTimersByTime(2000);
    expect(el.textContent).toBe(TEXT);
  });

  it("is a no-op while the motion flag is off", () => {
    setMotionEnabled(false);
    expect(isMotionEnabled()).toBe(false);

    const TEXT = "Addison's work";
    const el = leaf(TEXT);
    scrambleElement(el, 0);

    vi.advanceTimersByTime(2000);
    expect(el.textContent).toBe(TEXT);
  });

  it("ignores a second trigger while one is already running", () => {
    const TEXT = "Tokens this month";
    const el = leaf(TEXT);

    scrambleElement(el, 0);
    vi.advanceTimersByTime(38);
    const cancelSecond = scrambleElement(el, 0); // must not start a second loop
    cancelSecond(); // …so cancelling it must not stop the first one either

    vi.advanceTimersByTime(2000);
    expect(el.textContent).toBe(TEXT);
  });

  it("leaves an element with element children untouched", () => {
    const el = document.createElement("div");
    el.innerHTML = "<span>Tools</span>";
    document.body.appendChild(el);

    scrambleElement(el, 0);
    vi.advanceTimersByTime(2000);
    expect(el.innerHTML).toBe("<span>Tools</span>");
  });

  it("stops writing once cancelled", () => {
    const TEXT = "Build a widget";
    const el = leaf(TEXT);

    const cancel = scrambleElement(el, 0);
    vi.advanceTimersByTime(38);
    const frozen = el.textContent;
    cancel();

    vi.advanceTimersByTime(2000);
    expect(el.textContent).toBe(frozen);
  });
});
