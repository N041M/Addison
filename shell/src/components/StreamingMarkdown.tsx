// An answer that formats itself as it arrives (owner request 2026-08-21;
// reworked 2026-08-22 after the owner reported formatting that "breaks from time
// to time" and tables that stay raw pipes to the end).
//
// The rule: everything behind the cut is markdown, the rest is plain pre-wrap
// text. The cut is the last newline behind the scramble's resolved edge, so the
// parser only ever sees true text ending at a line break — the ~14 characters of
// random glyphs trailing the display (lib/scramble.ts) cannot reach it, and the
// half-written line cannot reflow under the reader. `lib/streamMarkdown.ts` owns
// where that cut is and why nothing falls out of it; this file owns WHEN to ask
// and how the answer keeps its identity between answers.
//
// FOUR THINGS THAT ARE NOT OBVIOUS:
//
//   * EVERY FRAME'S STRUCTURE IS A FRESH PARSE. No boundary is remembered and no
//     node is held back for being last. What that buys is that the stream can
//     never disagree with the final document: markdown is context-sensitive, so
//     a paragraph that settled before its underline arrived would stay a
//     paragraph while the true document had a setext heading — wrong on screen
//     until the turn landed and re-laid the whole answer out. Now the frame
//     after the underline reinterprets it, and the reader sees one block change
//     its mind rather than an answer that changes its mind at the end.
//   * BLOCKS ARE KEYED BY CONTENT, NOT BY POSITION. A byte-identical block keeps
//     its React element identity across frames, so the DOM node the reader is
//     selecting text in is not torn down and rebuilt 26 times a second, and
//     `Markdown`'s `memo` skips its re-parse. A block whose text changed —
//     because the document reinterpreted it — gets a different key, remounts
//     once, and is then correct. TWO IDENTICAL BLOCKS IN ONE ANSWER (two "Done."
//     paragraphs) would collide on a bare content hash, and duplicate React keys
//     corrupt reconciliation, so the key carries which occurrence of that hash
//     it is: the first keeps its identity and the duplicate is merely unique.
//   * THE PARSE IS THROTTLED TO A FRAME, THE TEXT IS NOT. The scramble emits a
//     frame every 38ms and re-parsing a long answer at that rate is work nobody
//     asked for, so a recompute is coalesced into one `requestAnimationFrame` —
//     with a 40ms timer booked under it, because that clock can be dead where
//     this app actually runs (the booking below owns the story).
//     Between frames the last cut is rendered against the CURRENT display
//     string, which keeps the arriving tail per-frame fresh while only the block
//     structure lags — the reader cannot see a boundary that is one frame old,
//     and would certainly see a tail that was. A stale cut stays safe for free:
//     past it everything is plain text, so glyphs only ever land in plain text.
//   * THE RESOLVED EDGE ONLY EVER MOVES FORWARD. It is a common-prefix length, so
//     a random glyph that happens to match the character it is standing in for
//     lengthens it for one frame and the next frame takes it back. Left alone,
//     a block whose end sits inside that flicker would settle into markdown and
//     fall out of it again, 26 times a second — the exact reflow this whole
//     design is avoiding. The clamp is on the EDGE rather than on the cut it
//     produces: raising a cut past the blocks that justify it would drop the
//     text in between, while a monotonic edge simply refuses to un-know
//     something it has already shown. With motion off, `display` IS `content`,
//     the edge is the whole length, and none of this needs a second path.
//
// WHAT THIS COSTS, said plainly. Each block is parsed on its own, so a link
// reference defined in one block (`[docs]: https://…`) is invisible to a block
// that uses it and renders literally until the turn settles — at which point
// ChatThread hands the whole message to one `Markdown` and it resolves. The
// block currently growing — a paragraph gaining lines, an open fence gaining
// lines — has different text every time a line completes, so it takes a new key
// and remounts: bounded by LINES, not by frames, and invisible for the common
// case of a paragraph that arrives as one line. And the answer is re-parsed from
// the top on each frame that runs, which is bounded by the frame clock but not
// by the answer's length; an answer long enough for that to be felt is also one
// the scramble is racing to catch up with.

import { memo, useEffect, useRef, useState } from "react";
import { Markdown } from "./Markdown";
import { commonPrefixLength, splitForStreaming, type StreamSplit } from "../lib/streamMarkdown";

interface Props {
  /** The true accumulated text — what the message actually holds. */
  content: string;
  /** What to show right now: the scramble's frame, or `content` when motion is off. */
  display: string;
  /** Ride the blinking block cursor after the tail. */
  showCursor: boolean;
}

/** How long the timer backstop waits — the scramble's own tick, so a dead frame
 * clock costs the parse nothing the animation wasn't already spending. */
const PARSE_FALLBACK_MS = 40;

function cancelBooking(booking: { frame: number | null; timer: ReturnType<typeof setTimeout> | null }) {
  if (booking.frame !== null && typeof cancelAnimationFrame === "function") {
    cancelAnimationFrame(booking.frame);
  }
  if (booking.timer !== null) clearTimeout(booking.timer);
  booking.frame = null;
  booking.timer = null;
}

function sameSplit(a: StreamSplit, b: StreamSplit): boolean {
  return (
    a.tailStart === b.tailStart &&
    a.blocks.length === b.blocks.length &&
    a.blocks.every((block, i) => block === b.blocks[i])
  );
}

// FNV-1a, 32-bit. Not a checksum anyone relies on — it only has to be cheap and
// to differ when the text differs. The length rides along in the key, and the
// occurrence counter makes even a true collision merely a lost memoisation
// rather than a duplicate key.
function hashBlock(block: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < block.length; i++) {
    h ^= block.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(36) + "-" + block.length;
}

/** Content keys for one frame's blocks, disambiguated by occurrence. */
function blockKeys(blocks: string[]): string[] {
  const seen = new Map<string, number>();
  return blocks.map((block) => {
    const hash = hashBlock(block);
    const nth = seen.get(hash) ?? 0;
    seen.set(hash, nth + 1);
    return `${hash}:${nth}`;
  });
}

function StreamingMarkdownImpl({ content, display, showCursor }: Props) {
  // The monotonic resolved edge (see the file header). A ref rather than state:
  // it is derived from the props of the render it is read in, and raising it
  // twice for one value is the same as raising it once, so a double render
  // cannot see a different answer than a single one.
  const edgeRef = useRef(0);
  function edge(trueText: string, shown: string): number {
    const measured = commonPrefixLength(trueText, shown);
    if (measured > edgeRef.current) edgeRef.current = measured;
    return edgeRef.current;
  }

  const [split, setSplit] = useState<StreamSplit>(() =>
    splitForStreaming(content, edge(content, display)),
  );

  // The props the pending recompute must read. It fires after this render has
  // committed, and by then `content` may already have grown again.
  const latest = useRef({ content, display });
  // The one recompute in flight, booked on BOTH clocks below. `null` when none.
  const booked = useRef<{ frame: number | null; timer: ReturnType<typeof setTimeout> | null }>({
    frame: null,
    timer: null,
  });

  useEffect(() => {
    latest.current = { content, display };
    const booking = booked.current;
    if (booking.frame !== null || booking.timer !== null) return;
    // Read from the ref, never from this render's props: by the time the
    // recompute runs, more of the answer may have arrived.
    const run = () => {
      cancelBooking(booking);
      const now = latest.current;
      const next = splitForStreaming(now.content, edge(now.content, now.display));
      setSplit((prev) => (sameSplit(prev, next) ? prev : next));
    };
    // Booked on TWO clocks, first one to fire wins and cancels the other. The
    // frame clock is the throttle this component wants — but it is a clock that
    // CAN SIMPLY NEVER TICK: wry's WKWebView leaves the page believing it is
    // hidden, so `requestAnimationFrame` accepts the callback and never runs it,
    // and the first build of this component — throttled on that clock alone —
    // never settled a single block in the real app while every jsdom test
    // passed (live failure, 2026-08-21; BUILD-LOG owns the finding). The timer
    // is the backstop: timers demonstrably fire in that webview — the scramble
    // itself is a 38ms interval — so the parse is carried at the scramble's own
    // cadence when the frame clock is dead, and the timer is cancelled unfired
    // everywhere the frame clock is alive.
    if (typeof requestAnimationFrame === "function") {
      booking.frame = requestAnimationFrame(run);
    }
    booking.timer = setTimeout(run, PARSE_FALLBACK_MS);
  }, [content, display]);

  useEffect(() => {
    // The booking OBJECT is stable — only its fields ever change — so taking it
    // once here reads the live state at unmount, not a stale copy.
    const booking = booked.current;
    return () => cancelBooking(booking);
  }, []);

  const limit = edge(content, display);
  // Belt and braces: the cut comes from a split that was already gated on the
  // edge, so it cannot exceed it — but a cut that ever did would reach into the
  // scrambled window, which is the one failure this component may not have.
  const tailStart = Math.min(split.tailStart, limit, display.length);
  // The tail begins where the last formatted block ended, so it opens with
  // whatever blank lines followed that block plus the line still being written
  // (`lib/streamMarkdown.ts` owns why the blank lines are on this side). A pre-wrap
  // element would render those as empty lines under the formatted text; the
  // parser ignores them, so only the plain text trims, and it trims for display
  // only — `content` is untouched.
  const tailText = display.slice(tailStart).replace(/^\n+/, "");

  const cursor = showCursor ? (
    // The block cursor riding the streamed tail (7×14px, blinking) — the same
    // element MessageRow puts after a plain body.
    <span
      aria-hidden="true"
      className="ml-[3px] inline-block h-[14px] w-[7px] animate-[blink_1.1s_step-start_infinite] bg-ink align-[-1px]"
    />
  ) : null;

  const keys = blockKeys(split.blocks);

  // ONE `.markdown-body`, never one per block: its `> :first-child` /
  // `> :last-child` margin trimming has to see the whole answer as a single
  // flow, or every block would trim its own margins and the answer would close
  // up (styles.css).
  return (
    <div className="markdown-body">
      {split.blocks.map((block, index) => (
        // `pending` keeps a completed mermaid fence as a code block until the
        // whole turn lands — a diagram is drawn once, not re-drawn under a
        // reader mid-answer.
        <Markdown key={keys[index]} content={block} pending bare />
      ))}
      {/* `data-msg-text` stays on the tail LEAF: it is what the
          conversation-switch scramble looks for, and it only ever animates an
          element with a single text node (ChatThread's file header). */}
      <p className="m-0 whitespace-pre-wrap">
        <span data-msg-text="1">{tailText}</span>
        {cursor}
      </p>
    </div>
  );
}

export const StreamingMarkdown = memo(StreamingMarkdownImpl);
