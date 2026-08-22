// Where an arriving answer may be cut into "already formatted" and "still
// arriving" (owner request 2026-08-21; reworked 2026-08-22). Pure: no React, no
// DOM, no timers — the component decides WHEN to ask, this file only answers
// WHERE.
//
// THE CUT IS ONE OFFSET, AND TWO FACTS PLACE IT:
//
//   1. THE SCRAMBLE'S RESOLVED EDGE (`limit`). What the reader sees is the true
//      text with a ~14-character window of random glyphs trailing it
//      (lib/scramble.ts). A glyph that reached the markdown parser would be
//      structure for one frame — a stray `#` as a heading, a stray `-` as a list
//      item — and the answer would reflow under the reader's eyes. Nothing at or
//      past `limit` is parsed, ever.
//   2. THE LAST NEWLINE BEHIND THAT EDGE. Only whole lines are parsed. The line
//      still being written is the one the parser would keep changing its mind
//      about — and half a table row is not a row at all, it is a paragraph that
//      breaks the table it belongs to — so it is withheld and the component
//      prints it as plain text under the formatted blocks.
//
// So the parse input is always TRUE text ending at a newline. That is the
// invariant this file exists for, and it now holds BY CONSTRUCTION: there is no
// input shape for which a glyph reaches the parser, so there is no shape needing
// an exception. The design this replaced had two — both for a fenced code block,
// the one thing that read better through the parser than beside it. It still
// does, and it costs nothing now: micromark closes an unclosed fence at end of
// input, so an open fence's completed lines simply parse as a code block that
// grows a line at a time, with the half-written line waiting below it in plain
// text. The gates went with the special case (`fenceEndOffset`, `tailIsFence`).
//
// EVERY BOUNDARY IS RE-DERIVED FROM A FRESH PARSE, EVERY TIME, and no node is
// held back for being last. Markdown is context-sensitive: "Results" is a
// paragraph until a line of dashes turns it into a setext heading, and a
// boundary frozen before those dashes arrived belongs to a document that no
// longer exists — the reader ends up looking at structure the answer does not
// have, until the turn settles and it all re-lays itself out. Because this
// function is stateless, what is on screen at any frame IS a parse of the prefix
// behind the cut; when the document reinterprets a block, the next frame
// reinterprets it too. A table therefore renders as soon as its header and
// separator lines are behind the cut, and grows row by row.
//
// NOTHING IS DROPPED. A block's slice runs from the PREVIOUS boundary to its own
// node's end rather than from its own start, so the blank line between two
// blocks rides at the front of the second slice instead of falling in the gap
// between them; leading newlines are inert to the parser and invisible in the
// output. The blank lines between the LAST block and the cut ride at the front
// of the tail on the same reasoning, and the component trims them for display
// exactly as it always has. Blocks joined with the tail reconstruct the input
// byte for byte, and `__tests__/streamMarkdown.test.tsx` pins that.
//
// A SLICE THEREFORE NEVER DEPENDS ON WHERE THE CUT FELL — only on its own node's
// position. That is not tidiness: `StreamingMarkdown` identifies a block by its
// BYTES (a content hash is its React key), so a slice that grew a newline every
// time the model finished a line would remount a settled block for a blank line
// that changes nothing about it. The only block whose bytes move is the one
// actually growing.

import { unified } from "unified";
import remarkParse from "remark-parse";
import remarkGfm from "remark-gfm";

// Frozen once. The same plugin set Markdown.tsx renders with — a boundary found
// by a parser that reads GFM tables differently from the one that renders them
// would cut in a place the renderer does not recognise.
const parser = unified().use(remarkParse).use(remarkGfm);

export interface StreamSplit {
  /** Top-level blocks, in order, safe to hand to the markdown renderer. */
  blocks: string[];
  /** Offset the still-arriving remainder begins at. */
  tailStart: number;
}

/** How many leading characters two strings share. */
export function commonPrefixLength(a: string, b: string): number {
  const n = Math.min(a.length, b.length);
  let i = 0;
  while (i < n && a[i] === b[i]) i++;
  return i;
}

/**
 * Cut `content` into renderable blocks plus the remainder still being written.
 *
 * `limit` is the scramble's resolved edge — the length of the prefix that is
 * known-true text. The parse runs over `content` up to the last newline at or
 * before `limit`, and the boundaries come from that parse alone: every node it
 * produced becomes a block, the last one included, and `tailStart` is where the
 * last of them ended (the blank lines after it, if any, open the tail).
 *
 * The ONE withhold is the trailing line with no newline yet. It is not a
 * completeness rule about blocks — a paragraph gaining a second line, a fence
 * gaining a line, a table gaining a row are all rendered while they grow — it is
 * a rule about the one line whose meaning is not yet decided.
 *
 * A node the parser gave no position for is not a node this can reason about,
 * and neither is anything after it: the loop stops there and the remainder goes
 * to the tail rather than guessing a boundary.
 */
export function splitForStreaming(content: string, limit: number): StreamSplit {
  const blocks: string[] = [];
  if (!content) return { blocks, tailStart: 0 };

  // Clamp defensively: the edge is a common-prefix length, so it cannot exceed
  // the content — but a cut past the end would parse text nobody has sent.
  const edge = Math.max(0, Math.min(limit, content.length));
  // `lastIndexOf` clamps a negative `fromIndex` to 0 rather than searching
  // nothing, so at edge 0 a leading newline would answer 0 and cut one character
  // PAST the edge. NO TEST WITNESSES THIS, and honestly none can: the character
  // in question is that newline, `"\n"` parses to no nodes, so the output is
  // identical either way. It is guarded because the rule is that the parser is
  // never handed text the frame has not resolved, and a rule with an arithmetic
  // exception in it stops being checkable by reading.
  const lastNewline = edge > 0 ? content.lastIndexOf("\n", edge - 1) : -1;
  // Nothing has finished a line behind the edge: the whole answer is still the
  // line being written.
  if (lastNewline === -1) return { blocks, tailStart: 0 };
  const cut = lastNewline + 1;

  const children = parser.parse(content.slice(0, cut)).children;
  let tailStart = 0;
  for (const node of children) {
    const end = node.position?.end.offset;
    if (end == null) break;
    blocks.push(content.slice(tailStart, end));
    tailStart = end;
  }

  return { blocks, tailStart };
}
