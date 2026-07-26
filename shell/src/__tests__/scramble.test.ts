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
  createStreamScramble,
  scrambleElement,
  setMotionEnabled,
  isMotionEnabled,
  prefersReducedMotion,
  STREAM_ADVANCE_CHARS,
  STREAM_WINDOW_CHARS,
  REVEAL_TARGET_MS,
  revealAdvanceFor,
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

// ---------------------------------------------------------------------------
// The streaming variant. The window math is worth pinning tick by tick because
// the failure it prevents is not a visual one: an engine that lets the window
// run past the received text would be rendering characters the model has not
// sent yet, which — in a scramble — means showing the person plausible glyphs in
// the position of words Addison has not actually written.
// ---------------------------------------------------------------------------
describe("createStreamScramble", () => {
  const TEXT = "Happy to help — here is where I landed after a first look at it.";

  function collect() {
    const frames: string[] = [];
    return { frames, engine: createStreamScramble((f) => frames.push(f)) };
  }

  it("advances the window five characters a tick and resolves what it leaves behind", () => {
    const { frames, engine } = collect();
    engine.push(TEXT);

    for (let tick = 1; tick <= 4; tick++) {
      vi.advanceTimersByTime(38);
      const frame = frames[frames.length - 1];
      const front = tick * STREAM_ADVANCE_CHARS;
      const resolved = Math.max(0, front - STREAM_WINDOW_CHARS);
      // The frame is exactly as long as the window's leading edge...
      expect(frame).toHaveLength(Math.min(TEXT.length, front));
      // ...its head is the real text, character for character...
      expect(frame.slice(0, resolved)).toBe(TEXT.slice(0, resolved));
      // ...and the tail behind it is not (something in there is still noise).
      if (front > resolved) expect(frame.slice(resolved)).not.toBe(TEXT.slice(resolved, front));
    }
  });

  it("never renders more than has been received, however long it runs", () => {
    const { frames, engine } = collect();
    const head = TEXT.slice(0, 20);
    engine.push(head);

    // Far longer than the window needs — the engine must idle at the tail rather
    // than inventing the rest of the sentence.
    vi.advanceTimersByTime(38 * 40);
    for (const frame of frames) expect(frame.length).toBeLessThanOrEqual(head.length);
    expect(frames[frames.length - 1]).toBe(head);

    // And when the rest arrives it picks up from there, still bounded.
    engine.push(TEXT);
    vi.advanceTimersByTime(38 * 40);
    for (const frame of frames) expect(frame.length).toBeLessThanOrEqual(TEXT.length);
  });

  it("lands on the exact received text", () => {
    const { frames, engine } = collect();
    engine.push(TEXT);
    vi.advanceTimersByTime(38 * 40);
    expect(frames[frames.length - 1]).toBe(TEXT);
  });

  it("passes whitespace through untouched", () => {
    const { frames, engine } = collect();
    const WITH_BREAK = "Reading your request\nand writing an answer for you now";
    engine.push(WITH_BREAK);

    vi.advanceTimersByTime(38 * 3);
    const frame = frames[frames.length - 1];
    for (let i = 0; i < frame.length; i++) {
      if (/\s/.test(WITH_BREAK[i])) expect(frame[i]).toBe(WITH_BREAK[i]);
    }
  });

  it("is a hard no-op with motion off — the text simply appends", () => {
    setMotionEnabled(false);
    const { frames, engine } = collect();

    engine.push("Happy to");
    engine.push("Happy to help");
    // No timer was ever scheduled: advancing changes nothing.
    vi.advanceTimersByTime(38 * 40);

    expect(frames).toEqual(["Happy to", "Happy to help"]);
  });

  it("stops emitting once torn down", () => {
    const { frames, engine } = collect();
    engine.push(TEXT);
    vi.advanceTimersByTime(38);
    engine.stop();
    const after = frames.length;

    vi.advanceTimersByTime(38 * 40);
    expect(frames).toHaveLength(after);
  });
});

// The whole-answer reveal (owner request 2026-07-26). The core sends an answer
// complete, so the rate has to adapt: the prototype's fixed 5 chars/tick reads
// well at demo length and turns a real 3,000-character answer into a 23-second
// wait. These pin the two ends of that rule, because getting either wrong is
// invisible in a screenshot and obvious in use.
describe("the whole-answer reveal rate", () => {
  it("keeps the prototype's tempo for a short answer", () => {
    expect(revealAdvanceFor(40)).toBe(STREAM_ADVANCE_CHARS);
    expect(revealAdvanceFor(0)).toBe(STREAM_ADVANCE_CHARS);
  });

  it("never lets a long answer outrun the target duration", () => {
    for (const length of [500, 3000, 20000]) {
      const advance = revealAdvanceFor(length);
      const ticks = Math.ceil((length + STREAM_WINDOW_CHARS) / advance);
      // A little slack for the trailing window; the point is it stays ~1s, not
      // that it hits the target to the millisecond.
      expect(ticks * 38).toBeLessThanOrEqual(REVEAL_TARGET_MS * 1.5);
    }
  });

  it("reveals at the requested rate and reports when it is done", () => {
    const frames: string[] = [];
    let done = 0;
    const text = "Done — I renamed the photos.";
    const engine = createStreamScramble((f) => frames.push(f), {
      advanceChars: 40,
      onDone: () => (done += 1),
    });
    engine.push(text);
    vi.advanceTimersByTime(38 * 10);

    expect(frames.at(-1)).toBe(text); // lands EXACTLY on the answer
    expect(done).toBe(1); // and says so, exactly once
    expect(frames.length).toBeLessThan(4); // 40 chars/tick: a few frames, not 9
  });
});
