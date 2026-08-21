// An answer that formats itself as it arrives (owner request 2026-08-21).
//
// The rule, and the whole reason this component exists: a block becomes markdown
// only once it is COMPLETE and lies entirely behind the scramble's resolved edge.
// Everything else — the block the model is still writing — stays plain pre-wrap
// text, so the ~14 characters of random glyphs trailing the text (lib/scramble.ts)
// never reach the markdown parser. `lib/streamMarkdown.ts` owns where that cut is
// made and why nothing falls out of it.
//
// TWO THINGS THAT ARE NOT OBVIOUS:
//
//   * THE PARSE IS THROTTLED TO A FRAME, THE TEXT IS NOT. The scramble emits a
//     frame every 38ms and re-parsing a long answer at that rate is work nobody
//     asked for, so a recompute is coalesced into one `requestAnimationFrame`.
//     Between frames the last boundaries are rendered against the CURRENT display
//     string, which is what keeps the arriving tail per-frame fresh while only
//     the block structure lags — the reader cannot see a boundary that is one
//     frame old, and would certainly see a tail that was.
//   * THE RESOLVED EDGE ONLY EVER MOVES FORWARD. It is a common-prefix length, so
//     a random glyph that happens to match the character it is standing in for
//     lengthens it for one frame and the next frame takes it back. Left alone,
//     a block whose end sits inside that flicker would settle into markdown and
//     fall out of it again, 26 times a second — the exact reflow this whole
//     design is avoiding. The clamp is on the EDGE rather than on the boundary
//     it produces: raising a boundary past the blocks that justify it would drop
//     the text in between, while a monotonic edge simply refuses to un-know
//     something it has already shown.
//
// WHAT THIS COSTS, said plainly. Each settled block is parsed on its own, so a
// link reference defined in one block (`[docs]: https://…`) is invisible to a
// block that uses it, and renders literally until the turn settles — at which
// point ChatThread hands the whole message to one `Markdown` and it resolves. And
// the answer is re-parsed from the top on each frame that runs, which is bounded
// by the frame clock but not by the answer's length; an answer long enough for
// that to be felt is also one the scramble is racing to catch up with.

import { memo, useEffect, useRef, useState } from "react";
import { Markdown } from "./Markdown";
import {
  commonPrefixLength,
  fenceEndOffset,
  splitForStreaming,
  type StreamSplit,
} from "../lib/streamMarkdown";

interface Props {
  /** The true accumulated text — what the message actually holds. */
  content: string;
  /** What to show right now: the scramble's frame, or `content` when motion is off. */
  display: string;
  /** Ride the blinking block cursor after the tail. */
  showCursor: boolean;
}

function sameSplit(a: StreamSplit, b: StreamSplit): boolean {
  return (
    a.tailStart === b.tailStart &&
    a.tailIsFence === b.tailIsFence &&
    a.settled.length === b.settled.length &&
    a.settled.every((block, i) => block === b.settled[i])
  );
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

  // The props the pending frame must read. It fires after this render has
  // committed, and by then `content` may already have grown again.
  const latest = useRef({ content, display });
  const frame = useRef<number | null>(null);

  useEffect(() => {
    latest.current = { content, display };
    if (frame.current !== null) return;
    // Read from the ref, never from this render's props: by the time the frame
    // runs, more of the answer may have arrived.
    const recompute = () => {
      const now = latest.current;
      const next = splitForStreaming(now.content, edge(now.content, now.display));
      setSplit((prev) => (sameSplit(prev, next) ? prev : next));
    };
    if (typeof requestAnimationFrame !== "function") {
      // No frame clock (a non-browser host). Nothing to coalesce against, so the
      // throttle is dropped rather than emulated with a timer.
      recompute();
      return;
    }
    frame.current = requestAnimationFrame(() => {
      frame.current = null;
      recompute();
    });
  }, [content, display]);

  useEffect(() => {
    return () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
      frame.current = null;
    };
  }, []);

  const limit = edge(content, display);
  // Belt and braces: the boundary comes from a split that was already gated on
  // the edge, so it cannot exceed it — but a boundary that ever did would cut
  // into the scrambled window, which is the one failure this component may not
  // have.
  const tailStart = Math.min(split.tailStart, limit, display.length);
  const rest = display.slice(tailStart);
  // The fence tail is cut at its own closing line, not taken whole. The split is
  // one frame old by design, so the model can close a fence and start the next
  // block before the recompute runs — and a "fence" tail that reached past the
  // close would hand that new block, glyphs and all, to the parser
  // (`fenceEndOffset` owns why a close in the display can be trusted). Whatever
  // lies past the cut is plain text this frame; the next parse settles it
  // properly.
  const fenceEnd = split.tailIsFence ? fenceEndOffset(rest) : 0;
  // The tail begins at the previous block's end, so it opens with that block's
  // blank-line separator. A pre-wrap element would render those as empty lines
  // under the formatted text; the parser ignores them, so only the plain text
  // trims, and it trims for display only — `content` is untouched.
  const tailText = rest.slice(fenceEnd).replace(/^\n+/, "");

  const cursor = showCursor ? (
    // The block cursor riding the streamed tail (7×14px, blinking) — the same
    // element MessageRow puts after a plain body.
    <span
      aria-hidden="true"
      className="ml-[3px] inline-block h-[14px] w-[7px] animate-[blink_1.1s_step-start_infinite] bg-ink align-[-1px]"
    />
  ) : null;

  // ONE `.markdown-body`, never one per block: its `> :first-child` /
  // `> :last-child` margin trimming has to see the whole answer as a single
  // flow, or every block would trim its own margins and the answer would close
  // up (styles.css).
  return (
    <div className="markdown-body">
      {split.settled.map((block, index) => (
        // `key={index}`: blocks are append-only and a settled one never changes,
        // so an index is a stable identity here. `pending` keeps a completed
        // mermaid fence as a code block until the whole turn lands — a diagram
        // is drawn once, not re-drawn under a reader mid-answer.
        <Markdown key={index} content={block} pending bare />
      ))}
      {split.tailIsFence && (
        // The fence is auto-closed by the parser at end of input, so an open
        // fence shows as a growing code block. THE GLYPH POOLS CONTAIN NO
        // BACKTICK AND NO NEWLINE (lib/scramble.ts), and whitespace passes
        // through untouched — so a scrambled frame inside a fence can neither
        // close it early nor mint a block that is not in the true text. That
        // is the fact this branch rests on; the split's own gates (everything
        // before the fence settled, opener line resolved) are what confine the
        // glyphs to the fence's interior in the first place.
        <Markdown content={rest.slice(0, fenceEnd)} pending bare />
      )}
      {/* `data-msg-text` stays on the tail LEAF: it is what the
          conversation-switch scramble looks for, and it only ever animates an
          element with a single text node (ChatThread's file header). Present
          after a fence too — usually empty there, it is where a stale frame's
          overflow lands. */}
      <p className="m-0 whitespace-pre-wrap">
        <span data-msg-text="1">{tailText}</span>
        {cursor}
      </p>
    </div>
  );
}

export const StreamingMarkdown = memo(StreamingMarkdownImpl);
