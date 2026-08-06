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
// TWO HABITS THIS FILE HOLDS TO, both learned from mutations that survived the
// whole suite (review 2026-07-26):
//
//   * A motion-off test asserts MID-FLIGHT and asserts that no timer was
//     scheduled. Advancing 2,000ms and finding the original string proves
//     nothing — the engine restores the original whether it animated or not, so
//     deleting the `isMotionEnabled()` check kept every one of these green.
//   * The numbers here are ABSOLUTE (5 chars a tick, a 14-character window, a
//     38ms tick, a ~1.1s reveal), never re-derived from the module's own
//     constants. A test that computes its expectation from the value under test
//     agrees with any value it is given.
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

    // Nothing was SCHEDULED. This is the assertion that means "off": the check
    // happens before any timer exists, so there is no loop to interrupt.
    expect(vi.getTimerCount()).toBe(0);
    // And mid-flight — where an animation would be showing glyphs — the text is
    // still the sentence. (The settled string at t=2000 is NOT evidence: the
    // engine restores the original either way, so that assertion alone passes
    // with the motion check deleted.)
    vi.advanceTimersByTime(76);
    expect(el.textContent).toBe(TEXT);
    vi.advanceTimersByTime(2000);
    expect(el.textContent).toBe(TEXT);
  });

  it("is a no-op while the motion flag is off", () => {
    setMotionEnabled(false);
    expect(isMotionEnabled()).toBe(false);

    const TEXT = "Addison's work";
    const el = leaf(TEXT);
    scrambleElement(el, 0);

    expect(vi.getTimerCount()).toBe(0);
    vi.advanceTimersByTime(76);
    expect(el.textContent).toBe(TEXT);
    vi.advanceTimersByTime(2000);
    expect(el.textContent).toBe(TEXT);
  });

  it("ignores a second trigger while one is already running", () => {
    const TEXT = "Tokens this month";
    const el = leaf(TEXT);

    scrambleElement(el, 0);
    vi.advanceTimersByTime(38); // start delay fired: exactly one loop is running
    expect(vi.getTimerCount()).toBe(1);

    // The guard has to be proven by what is SCHEDULED. Cancelling the second
    // handle instead proves nothing: a no-op cancel and a real one both leave
    // the first loop alive, so that version of this test agreed with a missing
    // guard (mutation, review 2026-07-26).
    scrambleElement(el, 0);
    expect(vi.getTimerCount()).toBe(1);

    vi.advanceTimersByTime(2000);
    expect(el.textContent).toBe(TEXT);
  });

  // Two loops over one text node is the corruption (3) exists to prevent, and a
  // rename is how it happens without a second loop: React writes new text into
  // the node a running scramble is animating. Restoring the string captured at
  // start would put the OLD title back, permanently, on the frame the animation
  // ends — a motion flourish silently undoing an edit the person just made.
  it("abandons a node whose text was replaced under it, keeping the new string", () => {
    const el = leaf("Weekend plans");

    scrambleElement(el, 0);
    vi.advanceTimersByTime(76); // mid-flight: the node holds a scrambled frame

    el.firstChild!.nodeValue = "Trip to Brno"; // the rename lands

    vi.advanceTimersByTime(2000);
    expect(el.textContent).toBe("Trip to Brno");
    expect(vi.getTimerCount()).toBe(0); // and the abandoned loop stopped itself
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

  // The first frame is emitted SYNCHRONOUSLY, inside `push`. The caller commits
  // "this message is being revealed" and this frame in one React batch; with the
  // first frame 38ms out instead, the settled answer was committed with no
  // overlay over it, so it rendered once in full — formatted markdown, final
  // layout — and dissolved into glyphs a frame later (review 2026-07-26).
  it("emits its first frame before any timer runs", () => {
    const { frames, engine } = collect();
    engine.push(TEXT);

    expect(frames).toHaveLength(1);
    expect(frames[0]).toHaveLength(5); // one tick's worth of window, at t=0
    expect(frames[0]).not.toBe(TEXT.slice(0, 5));
  });

  it("advances the window five characters a tick and resolves what it leaves behind", () => {
    const { frames, engine } = collect();
    engine.push(TEXT);

    // Absolute numbers on purpose: 5 characters a tick, a 14-character window.
    // Deriving them from the module's constants made this test agree with any
    // value those constants held (mutation, review 2026-07-26).
    for (let k = 0; k < 4; k++) {
      const frame = frames[k];
      const front = (k + 1) * 5;
      const resolved = Math.max(0, front - 14);
      // The frame is exactly as long as the window's leading edge...
      expect(frame).toHaveLength(Math.min(TEXT.length, front));
      // ...its head is the real text, character for character...
      expect(frame.slice(0, resolved)).toBe(TEXT.slice(0, resolved));
      // ...and the tail behind it is not (something in there is still noise).
      if (front > resolved) expect(frame.slice(resolved)).not.toBe(TEXT.slice(resolved, front));
      vi.advanceTimersByTime(38);
    }
  });

  // A stream with no fixed rate paces itself against the BACKLOG. The prototype's
  // flat 5 chars a tick reads well while text trickles in, but it is not a rate,
  // it is a cap: a burst the app is already holding whole would be dribbled out
  // at ~130 characters a second — 8,000 characters would take a minute, of an
  // answer that had completely arrived.
  it("catches up on a burst instead of dribbling it out at the trickle rate", () => {
    const { frames, engine } = collect();
    const BURST = "steady on, ".repeat(800).slice(0, 8000); // 8,000 characters

    engine.push(BURST);
    vi.advanceTimersByTime(38 * 30); // ~1.1s, the reveal budget

    // At a flat 5 chars a tick this would be ~155 of 8,000 characters. Compared
    // by length and by identity rather than with `toBe`, so a failure prints a
    // number instead of an 8,000-character diff.
    const last = frames[frames.length - 1];
    expect(last).toHaveLength(8000);
    expect(last === BURST).toBe(true);
    // ...and it never got there by inventing any of it.
    for (const frame of frames) expect(frame.length).toBeLessThanOrEqual(8000);
  });

  // A rate that is not a number leaves the window's leading edge non-finite, so
  // "have we caught up" is never true: the interval ticks for the life of the
  // app, emitting nothing — and in useTurn it also strands the revealing flag,
  // which permanently suppresses the overlay teardown.
  it("ignores a nonsense rate rather than spinning on it forever", () => {
    const frames: string[] = [];
    let done = 0;
    const engine = createStreamScramble((f) => frames.push(f), {
      advanceChars: Number.NaN,
      onDone: () => (done += 1),
    });

    engine.push(TEXT);
    vi.advanceTimersByTime(38 * 60);

    expect(frames[frames.length - 1]).toBe(TEXT);
    expect(done).toBe(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  // `onDone` is how a consumer learns the animation is over — useTurn drops its
  // display overlay on it, handing the message back to markdown. It reports once
  // per LANDING, and a paused-then-resumed stream lands more than once.
  //
  // This test used to assert the opposite ("done once, even when more text
  // arrives"), and that assertion was the bug: the core streams an answer as many
  // deltas, so the display catches up whenever the model pauses, and an engine
  // that reported only its first landing left the overlay up for good — the
  // answer stuck in plain pre-wrap text with a blinking cursor, asterisks and all
  // (owner screenshot 2026-08-06). The hazard the old rule was standing in for —
  // an emission reaching a torn-down consumer — is the next test, which holds it
  // without refusing to report a landing that really happened.
  it("reports done once per landing, and a resumed stream lands again", () => {
    const frames: string[] = [];
    let done = 0;
    const engine = createStreamScramble((f) => frames.push(f), { onDone: () => (done += 1) });

    engine.push("First half.");
    vi.advanceTimersByTime(38 * 20);
    expect(frames[frames.length - 1]).toBe("First half.");
    expect(done).toBe(1);
    // Idling on that landing does not report it again, however long it sits.
    vi.advanceTimersByTime(38 * 40);
    expect(done).toBe(1);

    engine.push("First half. Second half.");
    vi.advanceTimersByTime(38 * 20);
    expect(frames[frames.length - 1]).toBe("First half. Second half.");
    expect(done).toBe(2);
  });

  // `stop()` is a consumer saying it is finished with this engine. Both callbacks
  // close over React state setters, so an emission after teardown writes to a hook
  // that may be gone — and a `push` from a later turn must never resurrect the
  // engine an earlier one released.
  it("emits nothing at all once it has been torn down", () => {
    const frames: string[] = [];
    let done = 0;
    const engine = createStreamScramble((f) => frames.push(f), { onDone: () => (done += 1) });

    engine.push("First half.");
    vi.advanceTimersByTime(38 * 20);
    const after = frames.length;
    expect(done).toBe(1);

    engine.stop();
    engine.push("First half. Second half.");
    vi.advanceTimersByTime(38 * 40);

    expect(frames).toHaveLength(after);
    expect(done).toBe(1);
    expect(vi.getTimerCount()).toBe(0);
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
    // No timer was ever scheduled — the check runs before one can be.
    expect(vi.getTimerCount()).toBe(0);
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
    // 5 chars a tick, spelled out — see the "absolute numbers" note in the file
    // header for why this is not `STREAM_ADVANCE_CHARS`.
    expect(revealAdvanceFor(40)).toBe(5);
    expect(revealAdvanceFor(0)).toBe(5);
  });

  it("never lets a long answer outrun the target duration", () => {
    for (const length of [500, 3000, 20000]) {
      const advance = revealAdvanceFor(length);
      const ticks = Math.ceil((length + 14) / advance);
      // The reveal is budgeted at ~1.1s; 1,650ms is that budget with slack for
      // the trailing window. The point is it stays about a second whatever the
      // answer's length — 20,000 characters at the trickle rate is 2.5 minutes.
      expect(ticks * 38).toBeLessThanOrEqual(1650);
    }
  });

  it("reveals at the requested rate and reports when it is done", () => {
    const frames: string[] = [];
    let done = 0;
    const text = "Done — I renamed the photos."; // 28 characters
    const engine = createStreamScramble((f) => frames.push(f), {
      advanceChars: 40,
      onDone: () => (done += 1),
    });
    engine.push(text);
    vi.advanceTimersByTime(38 * 10);

    expect(frames.at(-1)).toBe(text); // lands EXACTLY on the answer
    expect(done).toBe(1); // and says so, exactly once
    // 40 chars a tick over 28 characters plus a 14-character window: the frame
    // in `push`, then one more that lands it. Not nine.
    expect(frames).toHaveLength(2);
  });
});
