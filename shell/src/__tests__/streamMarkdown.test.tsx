// An answer that formats itself as it arrives (owner request 2026-08-21;
// reworked 2026-08-22), and the two properties that make it both safe and
// honest:
//
//   * NO SCRAMBLED GLYPH EVER REACHES THE MARKDOWN PARSER. The parse input is
//     true text cut at a newline behind the resolved edge — the prefix the frame
//     and the truth still agree on — so a glyph cannot become structure. This
//     now holds by construction rather than by a gate, which is why the fence
//     special case and its two gates are gone.
//   * WHAT IS ON SCREEN IS A PARSE OF THE PREFIX, EVERY FRAME. No boundary is
//     frozen and no node is withheld for being last, so the stream can never
//     disagree with the final document: a paragraph that becomes a setext
//     heading corrects itself, and a table renders from its header rather than
//     waiting for a block after it.
//
// The splitter is a separate module because the first property is arithmetic on
// a common prefix, and arithmetic can be tested exhaustively where a rendering
// rule can only be sampled.
//
// Mermaid is stubbed, exactly as in chatThread.test.tsx: it is irrelevant here
// and drags a heavy async renderer into jsdom. The real Markdown component is
// what the blocks have to go through.

import { describe, it, expect, vi } from "vitest";
import { act, render } from "@testing-library/react";
import { commonPrefixLength, splitForStreaming } from "../lib/streamMarkdown";
import { StreamingMarkdown } from "../components/StreamingMarkdown";
import { Markdown } from "../components/Markdown";
import { createStreamScramble } from "../lib/scramble";

vi.mock("../components/MermaidDiagram", () => ({ MermaidDiagram: () => null }));

/** The rAF the component coalesces its re-parse into. */
async function flushFrame() {
  await act(async () => {
    await new Promise((resolve) => requestAnimationFrame(() => resolve(null)));
  });
}

/** Everything inside the one `.markdown-body`, as markup. */
function bodyHtml(container: HTMLElement): string {
  const body = container.querySelector(".markdown-body");
  return body ? [...body.children].map((el) => el.outerHTML).join("") : "";
}

/** The streamed answer's formatted blocks — its plain tail paragraph is always
 * the last child, and is what the comparison with a finished render excludes. */
function streamedBlocksHtml(container: HTMLElement): string {
  const body = container.querySelector(".markdown-body");
  if (!body) return "";
  return [...body.children]
    .slice(0, -1)
    .map((el) => el.outerHTML)
    .join("");
}

describe("commonPrefixLength", () => {
  it("counts the characters two strings agree on", () => {
    expect(commonPrefixLength("# Tidied", "# Tid%&d")).toBe(5);
    expect(commonPrefixLength("", "anything")).toBe(0);
    expect(commonPrefixLength("same", "same")).toBe(4);
    // The frame can be shorter than the truth — the display lags, never leads.
    expect(commonPrefixLength("Here is more", "Here is")).toBe(7);
  });
});

describe("splitForStreaming", () => {
  // The property everything else rests on: the cut moves text, it never loses
  // it. Block slices run boundary-to-boundary and the tail starts at the last
  // one's end, so the blank line between two blocks is inside a slice or at the
  // head of the tail, never in a gap between them. Checked at EVERY edge, not
  // just the settled one — every frame of a stream is one of these.
  it("accounts for every byte of the content, at every edge", () => {
    const samples = [
      "",
      "One paragraph only.",
      "No newline anywhere at all, still arriving",
      "A\n\nB\n\nC",
      "# Tidied\n\nI moved **24** files.\n\nAnything else?",
      "\n\n# leading blank lines\n\nand a body",
      "Intro.\n\n```python\nprint(1)\n",
      "```py\nx()\n```",
      "Results\n---\n",
      "Before.\n\n| a | b |\n| - | - |\n| 1 | 2 |\n\nAfter.",
      "- one\n- two\n\nAfter the list.",
    ];
    for (const content of samples) {
      for (let limit = 0; limit <= content.length; limit++) {
        const { blocks, tailStart } = splitForStreaming(content, limit);
        expect(blocks.join("") + content.slice(tailStart)).toBe(content);
      }
    }
  });

  // The ONE withhold. Not a rule about blocks being complete — a rule about the
  // one line whose meaning is not yet decided.
  it("withholds the line that has no newline yet", () => {
    const content = "First.\n\nSecond.";
    const { blocks, tailStart } = splitForStreaming(content, content.length);

    expect(blocks).toEqual(["First."]);
    expect(content.slice(tailStart)).toBe("\n\nSecond.");
  });

  // …and the moment that withhold ends. The old design refused the last node
  // however finished it looked, which is what kept a table raw to the end of an
  // answer that ended with one.
  it("renders the last node too, once its line has ended", () => {
    const content = "First.\n\nSecond.\n";
    const { blocks, tailStart } = splitForStreaming(content, content.length);

    expect(blocks).toEqual(["First.", "\n\nSecond."]);
    expect(content.slice(tailStart)).toBe("\n");
  });

  it("withholds everything while no line has ended at all", () => {
    const content = "A sentence that has not reached a newline";
    expect(splitForStreaming(content, content.length)).toEqual({ blocks: [], tailStart: 0 });
    // A resolved edge of zero settles nothing, whatever the text behind it is.
    // (It does NOT witness the `lastIndexOf` clamp the splitter guards against —
    // that one is unobservable from the outside, and the code says so.)
    expect(splitForStreaming("\n\n# A heading\n", 0)).toEqual({ blocks: [], tailStart: 0 });
  });

  it("settles nothing a scrambled window still overlaps", () => {
    const content = "# Tidied\n\nI moved 24 files.\n\nAnything else?";
    // The resolved edge stops one character inside the heading.
    expect(splitForStreaming(content, 7).blocks).toEqual([]);
    // Its text has cleared the edge, but its NEWLINE has not: the line is not
    // over, so the heading is still the line being written.
    expect(splitForStreaming(content, 8).blocks).toEqual([]);
    // Newline behind the edge: now it is a heading.
    expect(splitForStreaming(content, 9).blocks).toEqual(["# Tidied"]);
    // The second paragraph's line has ended too, so it comes with it.
    expect(splitForStreaming(content, 28).blocks).toEqual([
      "# Tidied",
      "\n\nI moved 24 files.",
    ]);
  });

  it("keeps a half-written table row out of the parse", () => {
    const content = "Before.\n\n| a | b |\n| - | - |\n| 1 ";
    const { blocks, tailStart } = splitForStreaming(content, content.length);

    // The header and separator rows are whole lines, so they are a table; the
    // partial row is not a line yet.
    expect(blocks).toEqual(["Before.", "\n\n| a | b |\n| - | - |"]);
    expect(content.slice(tailStart)).toBe("\n| 1 ");
  });
});

// ---------------------------------------------------------------------------
// What the reader actually sees, with the real Markdown component underneath.
// ---------------------------------------------------------------------------
describe("StreamingMarkdown", () => {
  it("formats the blocks behind the edge and leaves the rest as text", async () => {
    const content = "# Tidied\n\nI moved **24** files.\n\nAnything else";
    const { container } = render(
      <StreamingMarkdown content={content} display={content} showCursor />,
    );
    await flushFrame();

    expect(container.querySelector("h1")?.textContent).toBe("Tidied");
    expect(container.querySelector("strong")?.textContent).toBe("24");
    // The line still being written is plain, and NOT parsed.
    expect(container.querySelector("[data-msg-text]")?.textContent).toBe("Anything else");
    // Every block shares one wrapper, so the margin trimming sees one flow.
    expect(container.querySelectorAll(".markdown-body")).toHaveLength(1);
  });

  // THE NEW CENTRAL PROPERTY. Fed one increment at a time, the answer on screen
  // is always exactly what a fresh render of the same text produces — nothing
  // carried over from an earlier frame, nothing held back because of one. A
  // design that remembered a boundary would diverge the moment the document
  // reinterpreted it, and that divergence is invisible to every other test here.
  it("renders, at every increment, exactly what a fresh render of the same text does", async () => {
    const doc =
      "# Report\n\nResults\n---\n\n| a | b |\n| - | - |\n| 1 | 2 |\n\n- one\n- two\n\n```py\nx()\n```\n\nDone.\n";

    const { container, rerender } = render(
      <StreamingMarkdown content="" display="" showCursor />,
    );
    for (let n = 3; n <= doc.length; n += 3) {
      const prefix = doc.slice(0, n);
      rerender(<StreamingMarkdown content={prefix} display={prefix} showCursor />);
      await flushFrame();

      const fresh = render(<StreamingMarkdown content={prefix} display={prefix} showCursor />);
      expect(container.innerHTML).toBe(fresh.container.innerHTML);
      fresh.unmount();
    }
    // Not vacuous: the increments really did build the whole document.
    expect(container.querySelector("h1")?.textContent).toBe("Report");
    expect(container.querySelector("h2")?.textContent).toBe("Results");
    expect(container.querySelector("table")).not.toBeNull();
    expect(container.querySelector("pre code")?.className).toContain("language-py");
  });

  // The other half of the same claim, stated against the renderer the settled
  // message will use: once the last line has ended, the streamed structure IS
  // the document's structure, so nothing re-lays itself out when the turn lands.
  it("agrees with the finished document's own rendering", async () => {
    const doc =
      "# Report\n\nResults\n---\n\n| a | b |\n| - | - |\n| 1 | 2 |\n\n- one\n- two\n\nDone.\n";
    const { container } = render(<StreamingMarkdown content={doc} display={doc} showCursor />);
    await flushFrame();

    const reference = render(<Markdown content={doc} pending />);
    const streamed = streamedBlocksHtml(container);
    // Not vacuous: there is a document on both sides, and it has structure.
    expect(streamed).toContain("<table");
    expect(streamed).toBe(bodyHtml(reference.container));
    reference.unmount();
  });

  // Markdown is context-sensitive, and this is the case that proves a frozen
  // boundary is wrong rather than merely late: the same eight characters are a
  // paragraph until a line of dashes underneath them makes them a heading.
  it("corrects a paragraph that the next line turns into a heading", async () => {
    const asParagraph = "Results\n";
    const { container, rerender } = render(
      <StreamingMarkdown content={asParagraph} display={asParagraph} showCursor />,
    );
    await flushFrame();
    expect([...container.querySelectorAll("p")].map((p) => p.textContent)).toContain("Results");
    expect(container.querySelector("h2")).toBeNull();

    // The dashes have arrived but their line has not ended: they are the line
    // being written, so they wait in plain text rather than render as a rule.
    const dashes = "Results\n---";
    rerender(<StreamingMarkdown content={dashes} display={dashes} showCursor />);
    await flushFrame();
    expect(container.querySelector("hr")).toBeNull();
    expect(container.querySelector("[data-msg-text]")?.textContent).toBe("---");

    // The newline resolves the line, and the document reinterprets itself.
    const asHeading = "Results\n---\n";
    rerender(<StreamingMarkdown content={asHeading} display={asHeading} showCursor />);
    await flushFrame();
    expect(container.querySelector("h2")?.textContent).toBe("Results");
    expect([...container.querySelectorAll("p")].map((p) => p.textContent)).not.toContain(
      "Results",
    );
  });

  // The owner's report, pinned: a table used to stay raw pipes until a block
  // existed after it, so an answer that ENDED with a table never formatted it.
  it("renders a table from its header and grows it row by row", async () => {
    const header = "Here:\n\n| a | b |\n| - | - |\n";
    const { container, rerender } = render(
      <StreamingMarkdown content={header} display={header} showCursor />,
    );
    await flushFrame();

    expect([...container.querySelectorAll("th")].map((th) => th.textContent)).toEqual(["a", "b"]);
    expect(container.querySelectorAll("tbody tr")).toHaveLength(0);

    // A row that has not reached its newline is withheld — half a row would be
    // a paragraph, and a paragraph in the middle of a table ends the table.
    const partial = header + "| 1 ";
    rerender(<StreamingMarkdown content={partial} display={partial} showCursor />);
    await flushFrame();
    expect(container.querySelector("table")).not.toBeNull();
    expect(container.querySelectorAll("tbody tr")).toHaveLength(0);
    expect(container.querySelector("[data-msg-text]")?.textContent).toBe("| 1 ");

    // Completed, newline and all: the table grows, and nothing follows it.
    const row = header + "| 1 | 2 |\n";
    rerender(<StreamingMarkdown content={row} display={row} showCursor />);
    await flushFrame();
    expect([...container.querySelectorAll("tbody td")].map((td) => td.textContent)).toEqual([
      "1",
      "2",
    ]);
    expect(container.querySelector("[data-msg-text]")?.textContent).toBe("");
  });

  it("shows an unclosed fence as a code block, with the half-written line below it", async () => {
    const content = "Intro.\n\n```python\nprint(1)\n";
    const { container, rerender } = render(
      <StreamingMarkdown content={content} display={content} showCursor />,
    );
    await flushFrame();

    const paragraphs = [...container.querySelectorAll("p")].map((p) => p.textContent);
    expect(paragraphs).toContain("Intro.");
    // Nothing special-cases this: micromark closes an unclosed fence at end of
    // input, and the input is whole lines of true text.
    const code = container.querySelector("pre code");
    expect(code?.className).toContain("language-python");
    expect(code?.textContent).toBe("print(1)\n");
    // The cursor still rides the answer while a fence is open.
    expect(container.querySelector("[aria-hidden='true']")).not.toBeNull();

    // The line being written stays out of the code block until it ends.
    const growing = content + "print(2";
    rerender(<StreamingMarkdown content={growing} display={growing} showCursor />);
    await flushFrame();
    expect(container.querySelector("pre code")?.textContent).toBe("print(1)\n");
    expect(container.querySelector("[data-msg-text]")?.textContent).toBe("print(2");
  });

  it("parses nothing while no line has resolved", async () => {
    const content = "Para one.\n\n```python\nprint(1)\n";
    // The frame agrees on two characters; the rest is glyphs. No line has ended
    // behind the edge, so there is nothing to parse and the frame is on screen
    // exactly as the animation drew it.
    const frame = "Pa%& o&e.\n\n```python\nprint(1)\n";
    const { container } = render(
      <StreamingMarkdown content={content} display={frame} showCursor />,
    );
    await flushFrame();

    expect(container.querySelector("pre code")).toBeNull();
    expect(container.querySelector("[data-msg-text]")?.textContent).toBe(frame);
  });

  it("parses nothing past the resolved edge", async () => {
    // A frame that diverges from the truth inside the first block: five
    // characters agree, so no line is behind the edge and the frame is on
    // screen exactly as the animation drew it.
    const content = "# Tidied\n\nI moved **24** files.\n\nAnd tidied the rest.";
    const frame = "# Tid%&d\n\nI m*ved **24** f#les.";
    const { container } = render(
      <StreamingMarkdown content={content} display={frame} showCursor />,
    );
    await flushFrame();

    expect(container.querySelector("h1")).toBeNull();
    expect(container.querySelector("strong")).toBeNull();
    expect(container.querySelector("[data-msg-text]")?.textContent).toBe(frame);
  });

  // The edge bounds the parse, and the CUT bounds it to a whole line behind that
  // edge — which is a stricter thing, and the tests above cannot tell the two
  // apart because their frames diverge before the first newline (with no line
  // ended at all, both refuse everything). Here a line HAS ended, and the frame
  // is still resolving the one after it: formatting text the reader is currently
  // watching dissolve into glyphs is structure running ahead of the animation.
  it("formats no further than the last line the frame has resolved", async () => {
    const content = "# Tidied\n\nI moved 24 files.\n";
    const frame = "# Tidied\n\nI m*ved 24 f#les.\n";
    const { container } = render(
      <StreamingMarkdown content={content} display={frame} showCursor />,
    );
    await flushFrame();

    expect(container.querySelector("h1")?.textContent).toBe("Tidied");
    // Exactly one `p` — the plain tail. The still-scrambling line is not a
    // paragraph, formatted from the true text behind the reader's back.
    expect(container.querySelectorAll("p")).toHaveLength(1);
    expect(container.querySelector("[data-msg-text]")?.textContent).toBe("I m*ved 24 f#les.\n");
  });

  // The split is one frame old by design, and this is what that costs: between
  // re-parses the LAST cut is rendered against the CURRENT display. It is safe
  // for a structural reason rather than a lucky one — past the cut everything is
  // plain text, so a glyph can only ever land in plain text, whatever the model
  // sent in the meantime.
  it("keeps a stale frame's overflow in plain text", async () => {
    const open = "```py\na\n";
    const { container, rerender } = render(
      <StreamingMarkdown content={open} display={open} showCursor />,
    );
    await flushFrame();
    expect(container.querySelector("pre code")?.textContent).toBe("a\n");

    // The model closed the fence and began the next block in one delta, BEFORE
    // the re-parse runs. `# T%&iling` is a heading the reader never wrote.
    const closed = "```py\na\n```\n\n# T%&iling";
    rerender(<StreamingMarkdown content={closed} display={closed} showCursor />);

    expect(container.querySelector("pre code")?.textContent).toBe("a\n");
    expect(container.querySelector("h1")).toBeNull();
    expect(container.querySelector("[data-msg-text]")?.textContent).toBe("```\n\n# T%&iling");
  });

  // The parser is kept away from glyphs by arithmetic, but the glyphs themselves
  // are what make a frame harmless in the plain tail, and that is a fact about
  // another file: the pools hold no backtick, no tilde and no newline, so a
  // frame can neither close a fence nor mint a block. This samples the engine
  // rather than reading the pools (they are private, and rightly), which makes
  // it a guard against a glyph being ADDED to one, not a proof that none is
  // there.
  it("draws no glyph that could close a fence or open a block", () => {
    const source = "print(1) and more code, all one long line with no breaks in it";
    const real = Math.random.bind(Math);
    // An engine picks ONE pool for its whole run, so a single run samples a
    // third of the vocabulary — and a backtick added to either of the other two
    // would go unseen. The first `Math.random` of a run is that pick; steering it
    // is what turns a lucky sample into all three pools.
    for (const pick of [0, 0.4, 0.7]) {
      vi.useFakeTimers();
      let first = true;
      const dice = vi.spyOn(Math, "random").mockImplementation(() => {
        if (!first) return real();
        first = false;
        return pick;
      });
      const frames: string[] = [];
      const engine = createStreamScramble((frame) => frames.push(frame));
      engine.push(source);
      vi.advanceTimersByTime(38 * 30);
      engine.stop();
      dice.mockRestore();
      vi.useRealTimers();

      // Not vacuous: frames arrived, and they were noise rather than the text.
      expect(frames.length).toBeGreaterThan(1);
      expect(frames.some((frame) => frame !== source.slice(0, frame.length))).toBe(true);
      expect(frames.join("")).not.toMatch(/[`~\n]/);
    }
  });

  // The edge is a common-prefix length, so a glyph that happens to match the
  // character it stands in for lengthens it for one frame and the next frame
  // takes it back. A block whose end sits in that flicker would settle and
  // un-settle 26 times a second, which is the reflow the whole design avoids.
  it("does not un-format a block when the measured edge slips back", async () => {
    const content = "# Tidied\n\nStill arriving";
    const { container, rerender } = render(
      <StreamingMarkdown content={content} display={content} showCursor />,
    );
    await flushFrame();
    expect(container.querySelector("h1")?.textContent).toBe("Tidied");

    // The next frame agrees on far less — the heading is inside the window again.
    rerender(<StreamingMarkdown content={content} display="# Ti%&ed\n\nStill" showCursor />);
    await flushFrame();

    expect(container.querySelector("h1")?.textContent).toBe("Tidied");
  });

  it("clamps the tail to a display that has fallen behind the cut", async () => {
    const content = "First.\n\nSecond.\n";
    const { container, rerender } = render(
      <StreamingMarkdown content={content} display={content} showCursor />,
    );
    await flushFrame();
    expect([...container.querySelectorAll("p")].map((p) => p.textContent)).toContain("Second.");

    // A frame shorter than the cut itself: the blocks stand, and the tail is
    // empty rather than sliced out of thin air.
    rerender(<StreamingMarkdown content={content} display="First." showCursor />);
    await flushFrame();
    expect([...container.querySelectorAll("p")].map((p) => p.textContent)).toContain("Second.");
    expect(container.querySelector("[data-msg-text]")?.textContent).toBe("");
  });

  // The live failure of 2026-08-21, pinned. wry's WKWebView leaves the page
  // believing it is hidden, so `requestAnimationFrame` ACCEPTS every callback
  // and fires none — and a re-parse throttled on that clock alone never settled
  // one block in the real app while this whole file stayed green (every other
  // test here flushes the frame by hand, which is exactly the tick the webview
  // never delivered). The timer backstop is what this pins: with a dead frame
  // clock, one timer tick must still bring the parse.
  it("still settles when the frame clock never ticks", async () => {
    const realRaf = window.requestAnimationFrame;
    const realCaf = window.cancelAnimationFrame;
    window.requestAnimationFrame = (() => 1) as typeof window.requestAnimationFrame;
    window.cancelAnimationFrame = () => {};
    vi.useFakeTimers();
    try {
      const { container, rerender } = render(
        <StreamingMarkdown content="# Tid" display="# Tid" showCursor />,
      );
      const content = "# Tidied\n\nStill arriving";
      rerender(<StreamingMarkdown content={content} display={content} showCursor />);
      // The recompute is booked and the frame clock is dead: nothing yet.
      expect(container.querySelector("h1")).toBeNull();

      await act(async () => {
        vi.advanceTimersByTime(50);
      });

      expect(container.querySelector("h1")?.textContent).toBe("Tidied");
    } finally {
      vi.useRealTimers();
      window.requestAnimationFrame = realRaf;
      window.cancelAnimationFrame = realCaf;
    }
  });

  it("keeps the tail per-frame fresh between re-parses", () => {
    // No frame is flushed here at all: the boundaries are whatever the last
    // parse decided, and the tail is still sliced out of the CURRENT display.
    const content = "First.\n\nSecond sentence arriving";
    const { container, rerender } = render(
      <StreamingMarkdown content={content} display={content} showCursor />,
    );

    const longer = content + " with more on the end";
    rerender(<StreamingMarkdown content={longer} display={longer} showCursor />);

    expect(container.querySelector("[data-msg-text]")?.textContent).toContain(
      "with more on the end",
    );
  });

  // What the content-hash keys buy, and it is what a reader can feel: a block
  // torn down and rebuilt loses the selection they were dragging over it and,
  // once a diagram is in one, redraws it. An index key would survive this case;
  // it is the case where a block's POSITION shifts that an index loses, and the
  // hash is what makes identity follow the bytes instead. Note what this does
  // NOT pin — whether `Markdown`'s `memo` skipped the re-parse is invisible from
  // here, because React writes nothing to the DOM for markup that came out
  // identical either way. That saving is real and this test is not its witness.
  it("does not tear down a block that has already settled", async () => {
    const content = "First.\n\nSecond arriving";
    const { container, rerender } = render(
      <StreamingMarkdown content={content} display={content} showCursor />,
    );
    await flushFrame();
    const first = container.querySelector("p");
    expect(first?.textContent).toBe("First.");

    const longer = "First.\n\nSecond is done.\n\nThird arriving";
    rerender(<StreamingMarkdown content={longer} display={longer} showCursor />);
    await flushFrame();

    // The same DOM node, not an equal one: the block's bytes did not change, so
    // re-parsing the answer must not cost the reader the block they are reading.
    expect(container.querySelector("p")).toBe(first);
    expect([...container.querySelectorAll("p")].map((p) => p.textContent)).toContain(
      "Second is done.",
    );
  });

  // A content hash alone would give two identical blocks the SAME React key, and
  // duplicate keys do not merely warn — they corrupt reconciliation, so one of
  // the two paragraphs would go missing or stop updating. The occurrence counter
  // is what keeps the first one's identity and the second one merely unique.
  it("keeps two byte-identical blocks apart", async () => {
    const warned = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      // Note the leading block: a slice carries the blank line that PRECEDES it,
      // so the first block of an answer is never byte-identical to a later one.
      // Two middle blocks are, and that is the collision.
      const content = "Ready.\n\nDone.\n\nDone.\n\nStill arriving";
      const { container } = render(
        <StreamingMarkdown content={content} display={content} showCursor />,
      );
      await flushFrame();

      const paragraphs = [...container.querySelectorAll("p")].map((p) => p.textContent);
      expect(paragraphs.filter((text) => text === "Done.")).toHaveLength(2);
      expect(warned.mock.calls.map((call) => String(call[0])).join(" ")).not.toMatch(
        /same key|duplicate/i,
      );
    } finally {
      warned.mockRestore();
    }
  });

  it("holds a completed mermaid fence as code until the whole turn lands", async () => {
    const content = "Here:\n\n```mermaid\ngraph TD; A-->B;\n```\n\nMore to come";
    const { container } = render(
      <StreamingMarkdown content={content} display={content} showCursor />,
    );
    await flushFrame();

    // MermaidDiagram is stubbed to render nothing, so its presence would be an
    // ABSENCE here — the code block is what proves the diagram was not drawn.
    expect(container.querySelector("pre code")?.textContent).toBe("graph TD; A-->B;\n");
  });

  it("renders the cursor alone before any text has arrived", async () => {
    const { container } = render(<StreamingMarkdown content="" display="" showCursor />);
    await flushFrame();

    expect(container.querySelector("[data-msg-text]")?.textContent).toBe("");
    expect(container.querySelector("[aria-hidden='true']")).not.toBeNull();
  });
});
