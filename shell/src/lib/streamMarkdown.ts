// Where an arriving answer may be cut into "already formatted" and "still
// arriving" (owner request 2026-08-21). Pure: no React, no DOM, no timers — the
// component decides WHEN to ask, this file only answers WHERE.
//
// THE CUT IS MADE AGAINST TWO EDGES, AND BOTH ARE NECESSARY:
//
//   1. A BLOCK EDGE. Only a complete top-level block may be formatted, so the
//      last node in the parse is always left alone — it is the one the model is
//      still writing, and a half-written table, link or list would otherwise
//      reflow on every delta as the parser changed its mind about it.
//   2. THE SCRAMBLE'S RESOLVED EDGE (`limit`). The display the reader sees is
//      the true text with a ~14-character window of random glyphs trailing it
//      (lib/scramble.ts), and a glyph that reached the markdown parser would be
//      structure for one frame — a stray `#` as a heading, a stray `-` as a list
//      item. `limit` is the length of the prefix the display and the truth still
//      agree on, so a block that ends at or before it contains no glyph by
//      construction.
//
// NOTHING IS DROPPED. A settled slice runs from the PREVIOUS boundary to its own
// node's end rather than from its own start, so the blank line between two blocks
// rides at the front of the second slice instead of falling in the gap between
// them; leading newlines are inert to the parser and invisible in the output. The
// tail slice starts at the last settled boundary for the same reason. Settled
// slices joined with the tail slice reconstruct the input byte for byte, and
// `__tests__/streamMarkdown.test.tsx` pins that.

import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";

// Frozen once. The same plugin set Markdown.tsx renders with — a boundary found
// by a parser that reads GFM tables differently from the one that renders them
// would cut in a place the renderer does not recognise.
const parser = unified().use(remarkParse).use(remarkGfm);

export interface StreamSplit {
  /** Complete blocks, in order, safe to hand to the markdown renderer. */
  settled: string[];
  /** Offset the still-arriving remainder begins at. */
  tailStart: number;
  /** The remainder is a fenced code block (see `splitForStreaming`). */
  tailIsFence: boolean;
}

/** How many leading characters two strings share. */
export function commonPrefixLength(a: string, b: string): number {
  const n = Math.min(a.length, b.length);
  let i = 0;
  while (i < n && a[i] === b[i]) i++;
  return i;
}

/**
 * Cut `content` into settled blocks plus the remainder still being written.
 *
 * `limit` is the scramble's resolved edge — the length of the prefix that is
 * known-true text. A block settles only if it is NOT the last node and it ends
 * at or before `limit`.
 *
 * `tailIsFence` says the remainder is a fenced code block, which is the one kind
 * of half-written block that renders better through the parser than beside it:
 * micromark closes an unclosed fence at end of input, so a fence the model has
 * opened and not yet closed shows as a code block that grows, instead of as three
 * backticks and some source. Nothing else is special-cased — a half-written link
 * or a table mid-row is simply "the last node" and stays plain text until the
 * block after it exists.
 *
 * The fence claim carries TWO EXTRA GATES, because it is the one claim that puts
 * display text — glyphs included — through the parser:
 *
 *   * THE OPENER LINE MUST BE FULLY BEHIND THE EDGE. A fence whose opener is
 *     still inside the scrambled window is not a fence in the display at all
 *     (the pools hold no backtick), so the parser would read its interior as
 *     ordinary lines. This is the gate that does the work, and it also refuses
 *     the neighbouring failure: an earlier block that has not cleared the edge
 *     means the edge is still inside that block, which puts it short of the
 *     opener by arithmetic — so a tail that still holds scrambled non-fence
 *     text can never be called "a fence" through this gate alone.
 *   * EVERYTHING BEFORE THE FENCE MUST HAVE SETTLED — the same refusal stated
 *     directly, rather than inherited from that arithmetic. It acts alone in
 *     exactly one place: a node the parser gave no position stops the settling
 *     loop early, and the opener gate reasons from positions, so this is the
 *     condition that still says no there.
 */
export function splitForStreaming(content: string, limit: number): StreamSplit {
  const settled: string[] = [];
  let tailStart = 0;
  if (!content) return { settled, tailStart, tailIsFence: false };

  const children = parser.parse(content).children;
  if (children.length === 0) return { settled, tailStart, tailIsFence: false };

  for (let i = 0; i < children.length - 1; i++) {
    const end = children[i].position?.end.offset;
    // A node with no position is not a node this can reason about, and neither
    // is anything after it: stop rather than guess a boundary.
    if (end == null || end > limit) break;
    settled.push(content.slice(tailStart, end));
    tailStart = end;
  }

  const last = children[children.length - 1];
  const start = last.position?.start.offset ?? 0;
  const opener = content.slice(start, start + 3);
  const openerLineEnd = content.indexOf("\n", start);
  const tailIsFence =
    last.type === "code" &&
    (opener === "```" || opener === "~~~") &&
    // The two gates from the header: the fence must be the ONLY node in the
    // tail (nothing still-scrambled rides ahead of it into the parser), and its
    // opener line must be known-true text, newline and all — an opener the
    // display has not finished resolving is not an opener in the display.
    settled.length === children.length - 1 &&
    openerLineEnd !== -1 &&
    openerLineEnd <= limit;

  return { settled, tailStart, tailIsFence };
}

/**
 * Where a fence-shaped tail stops being a fence: the offset just past its
 * closing line, or the tail's whole length while the fence is still open.
 *
 * This exists for a one-frame gap: the split is recomputed once per animation
 * frame, so when the model closes a fence and starts the next block within a
 * single delta, the render between the two still holds a split that says "the
 * tail is a fence" — and handing the WHOLE tail to the parser on that frame
 * would put the new block's scrambled text through it. Cutting at the closing
 * line keeps the code block stable and leaves the overflow as plain text until
 * the next parse catches up. A closing fence can be trusted when it is seen:
 * the glyph pools hold no backtick and no tilde, so a frame can neither mint a
 * close that is not in the true text nor un-mint one that is — a real close
 * still inside the window merely shows as glyphs, and the overflow stays
 * literal fence content for those frames, styled as code and nothing more.
 */
export function fenceEndOffset(tail: string): number {
  let offset = 0;
  let marker: string | null = null;
  for (const line of tail.split("\n")) {
    if (marker === null) {
      // Lines before the opener are the blank separator the tail carries.
      const found = /^ {0,3}(`{3,}|~{3,})/.exec(line);
      if (found) marker = found[1];
    } else {
      // A close matches the opener's character and is at least as long
      // (CommonMark's rule), with nothing else on the line.
      const close = /^ {0,3}(`{3,}|~{3,})[ \t]*$/.exec(line);
      if (close && close[1][0] === marker[0] && close[1].length >= marker.length) {
        // Past the closing line and its newline, clamped for a close that ends
        // the string.
        return Math.min(tail.length, offset + line.length + 1);
      }
    }
    offset += line.length + 1;
  }
  return tail.length;
}
