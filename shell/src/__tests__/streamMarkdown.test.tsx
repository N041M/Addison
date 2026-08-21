// An answer that formats itself as it arrives (owner request 2026-08-21), and
// the two edges that make it safe: a block is markdown only once it is COMPLETE
// and lies entirely behind the scramble's resolved edge.
//
// The invariant these tests exist for is the one in ChatThread's file header — a
// scrambled glyph must never reach the markdown parser — and the second half of
// it is the reason the splitter is a separate module: the gate is arithmetic on
// a common prefix, and arithmetic can be tested exhaustively where a rendering
// rule can only be sampled.
//
// Mermaid is stubbed, exactly as in chatThread.test.tsx: it is irrelevant here
// and drags a heavy async renderer into jsdom. The real Markdown component is
// what the settled blocks have to go through.

import { describe, it, expect, vi } from "vitest";
import { act, render } from "@testing-library/react";
import { commonPrefixLength, fenceEndOffset, splitForStreaming } from "../lib/streamMarkdown";
import { StreamingMarkdown } from "../components/StreamingMarkdown";
import { createStreamScramble } from "../lib/scramble";

vi.mock("../components/MermaidDiagram", () => ({ MermaidDiagram: () => null }));

/** The rAF the component coalesces its re-parse into. */
async function flushFrame() {
  await act(async () => {
    await new Promise((resolve) => requestAnimationFrame(() => resolve(null)));
  });
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
  // it. Settled slices run boundary-to-boundary and the tail starts at the last
  // boundary, so the blank line between two blocks is inside a slice rather
  // than in the gap between two of them.
  it("accounts for every byte of the content", () => {
    const samples = [
      "",
      "One paragraph only.",
      "A\n\nB\n\nC",
      "# Tidied\n\nI moved **24** files.\n\nAnything else?",
      "\n\n# leading blank lines\n\nand a body",
      "Intro.\n\n```python\nprint(1)\n",
      "Before.\n\n| a | b |\n| - | - |\n| 1 | 2 |\n\nAfter.",
      "- one\n- two\n\nAfter the list.",
    ];
    for (const content of samples) {
      const { settled, tailStart } = splitForStreaming(content, content.length);
      expect(settled.join("") + content.slice(tailStart)).toBe(content);
    }
  });

  it("never settles the last node, however complete it looks", () => {
    const content = "First.\n\nSecond.";
    const { settled, tailStart } = splitForStreaming(content, content.length);

    expect(settled).toEqual(["First."]);
    // The model may still be mid-sentence in it, so it stays tail — separator
    // and all, which is what keeps the reconstruction exact.
    expect(content.slice(tailStart)).toBe("\n\nSecond.");
  });

  it("settles nothing a scrambled window still overlaps", () => {
    const content = "# Tidied\n\nI moved 24 files.\n\nAnything else?";
    // The resolved edge stops one character inside the heading.
    expect(splitForStreaming(content, 7).settled).toEqual([]);
    // …and clears it.
    expect(splitForStreaming(content, 8).settled).toEqual(["# Tidied"]);
    // The second paragraph is complete and behind the edge, so it settles too.
    expect(splitForStreaming(content, 27).settled).toEqual(["# Tidied", "\n\nI moved 24 files."]);
  });

  it("reports a fence the model has opened and not yet closed", () => {
    const split = splitForStreaming("Intro.\n\n```python\nprint(1)\n", 27);
    expect(split.settled).toEqual(["Intro."]);
    expect(split.tailIsFence).toBe(true);
  });

  it("reports a closed fence as a fence too — it simply renders complete", () => {
    const content = "```js\nx()\n```";
    expect(splitForStreaming(content, content.length).tailIsFence).toBe(true);
  });

  // The fence claim is the one claim that puts display text through the parser,
  // so it carries two gates the other shapes do not (the header of
  // `splitForStreaming`). This first frame is refused by EITHER gate — an edge
  // inside the paragraph is short of the opener by arithmetic — so it kills the
  // ungated original, not one gate at a time; the opener test below is the one
  // that isolates the working gate.
  it("withholds the fence claim while unsettled text still stands before the fence", () => {
    const content = "Para.\n\n```py\ncode";
    // The edge is three characters into the first paragraph: the paragraph has
    // not settled, so the tail holds it too — and a tail that is "a fence"
    // would hand that paragraph's scrambled frame to the parser.
    expect(splitForStreaming(content, 3).tailIsFence).toBe(false);
    // Once the paragraph clears the edge, the fence is the whole tail again.
    expect(splitForStreaming(content, content.length).tailIsFence).toBe(true);
  });

  it("withholds the fence claim until the opener line has resolved", () => {
    const content = "```py\ncode";
    // Two characters of the opener are known-true; the rest is still glyphs in
    // the display, and glyphs hold no backtick — so the display has no fence,
    // and the parser would read `code` as an ordinary line.
    expect(splitForStreaming(content, 2).tailIsFence).toBe(false);
    // The opener line, newline and all, behind the edge: now it is a fence.
    expect(splitForStreaming(content, 6).tailIsFence).toBe(true);
  });

  it("treats an indented code block as ordinary text, not as a fence", () => {
    // `tailIsFence` is about the auto-close, and there is nothing to auto-close
    // here: an indented block ends where the indentation does.
    const content = "Intro.\n\n    print(1)";
    expect(splitForStreaming(content, content.length).tailIsFence).toBe(false);
  });

  it("leaves a table mid-row in the tail", () => {
    const content = "Before.\n\n| a | b |\n| - | - |\n| 1 ";
    const { settled, tailStart, tailIsFence } = splitForStreaming(content, content.length);

    expect(settled).toEqual(["Before."]);
    expect(content.slice(tailStart)).toBe("\n\n| a | b |\n| - | - |\n| 1 ");
    expect(tailIsFence).toBe(false);
  });

  it("leaves a half-written link in the tail", () => {
    const content = "See.\n\nHere is a [half link](htt";
    const { settled, tailStart } = splitForStreaming(content, content.length);

    expect(settled).toEqual(["See."]);
    expect(content.slice(tailStart)).toBe("\n\nHere is a [half link](htt");
  });
});

describe("fenceEndOffset", () => {
  it("reaches the end of the tail while the fence is open", () => {
    const tail = "\n\n```python\nprint(1)\n";
    expect(fenceEndOffset(tail)).toBe(tail.length);
  });

  it("stops just past the closing line, leaving what follows outside", () => {
    const tail = "```py\na\n```\n\n# not code";
    expect(tail.slice(0, fenceEndOffset(tail))).toBe("```py\na\n```\n");
  });

  it("includes a close that ends the string", () => {
    const tail = "```py\na\n```";
    expect(fenceEndOffset(tail)).toBe(tail.length);
  });

  it("honours CommonMark's closing rules — same character, at least as long", () => {
    // A tilde fence is not closed by backticks…
    expect(fenceEndOffset("~~~\n```\nstill code")).toBe("~~~\n```\nstill code".length);
    // …nor a four-marker opener by a three-marker line.
    expect(fenceEndOffset("````\n```\nstill code")).toBe("````\n```\nstill code".length);
    // A longer close than the opener does close it.
    const tail = "```\na\n````\nafter";
    expect(tail.slice(0, fenceEndOffset(tail))).toBe("```\na\n````\n");
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
    // The last block is still arriving: plain, and NOT parsed.
    expect(container.querySelector("[data-msg-text]")?.textContent).toBe("Anything else");
    // Every block shares one wrapper, so the margin trimming sees one flow.
    expect(container.querySelectorAll(".markdown-body")).toHaveLength(1);
  });

  it("shows an unclosed fence as a code block, with nothing swallowed after it", async () => {
    const content = "Intro.\n\n```python\nprint(1)\n";
    const { container } = render(
      <StreamingMarkdown content={content} display={content} showCursor />,
    );
    await flushFrame();

    const paragraphs = [...container.querySelectorAll("p")].map((p) => p.textContent);
    expect(paragraphs).toContain("Intro.");
    const code = container.querySelector("pre code");
    expect(code?.className).toContain("language-python");
    expect(code?.textContent).toBe("print(1)\n");
    // The cursor still rides the answer while a fence is open.
    expect(container.querySelector("[aria-hidden='true']")).not.toBeNull();
  });

  it("keeps the whole frame plain while a block before the fence is still resolving", async () => {
    const content = "Para one.\n\n```python\nprint(1)\n";
    // The frame agrees on two characters; the rest of the paragraph is glyphs.
    // The last node in the TRUE text is a fence — but the tail holds the
    // unsettled paragraph too, so calling it a fence would put that scrambled
    // paragraph through the parser. Nothing may be parsed here at all.
    const frame = "Pa%& o&e.\n\n```python\nprint(1)\n";
    const { container } = render(
      <StreamingMarkdown content={content} display={frame} showCursor />,
    );
    await flushFrame();

    expect(container.querySelector("pre code")).toBeNull();
    expect(container.querySelector("[data-msg-text]")?.textContent).toBe(frame);
  });

  it("does not parse past a fence that closed since the last re-parse", async () => {
    // Frame one: an open fence, parsed and on screen as code.
    const open = "```py\na\n";
    const { container, rerender } = render(
      <StreamingMarkdown content={open} display={open} showCursor />,
    );
    await flushFrame();
    expect(container.querySelector("pre code")?.textContent).toBe("a\n");

    // Frame two, BEFORE the re-parse runs: the model closed the fence and began
    // the next block in one delta. The split still says "the tail is a fence" —
    // and a fence tail taken whole would hand the new block's scrambled text to
    // the parser: `# T%&iling` is a heading the reader never wrote.
    const closed = "```py\na\n```\n\n# T%&iling";
    rerender(<StreamingMarkdown content={closed} display={closed} showCursor />);

    // The code block is intact, the overflow is plain text, and no heading
    // exists on this frame.
    expect(container.querySelector("pre code")?.textContent).toBe("a\n");
    expect(container.querySelector("h1")).toBeNull();
    expect(container.querySelector("[data-msg-text]")?.textContent).toBe("# T%&iling");
  });

  // The fence branch is the ONE place a scrambled glyph is handed to the parser,
  // and it is safe for a reason that lives in another file: the glyph pools hold
  // no backtick, no tilde and no newline, so a frame inside a fence can neither
  // close it early nor mint a block. This samples the engine rather than reading
  // the pools (they are private, and rightly), which makes it a guard against a
  // glyph being ADDED to one, not a proof that none is there.
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

  it("keeps a table out of the DOM until the block after it exists", async () => {
    const midRow = "Before.\n\n| a | b |\n| - | - |\n| 1 ";
    const { container, rerender } = render(
      <StreamingMarkdown content={midRow} display={midRow} showCursor />,
    );
    await flushFrame();

    expect(container.querySelector("table")).toBeNull();
    expect(container.querySelector("[data-msg-text]")?.textContent).toBe(
      "| a | b |\n| - | - |\n| 1 ",
    );

    const done = "Before.\n\n| a | b |\n| - | - |\n| 1 | 2 |\n\nAfter.";
    rerender(<StreamingMarkdown content={done} display={done} showCursor />);
    await flushFrame();

    expect(container.querySelector("table")).not.toBeNull();
    expect(container.querySelector("[data-msg-text]")?.textContent).toBe("After.");
  });

  it("parses nothing past the resolved edge", async () => {
    // A frame that diverges from the truth inside the first block: five
    // characters agree, so no block is behind the edge and the frame is on
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

  // The claim is DOM identity, which is what a reader can feel: a settled block
  // torn down and rebuilt loses the selection they were dragging over it and,
  // once a diagram is in one, redraws it. It is what an unstable key costs. Note
  // what it does NOT pin — whether `Markdown`'s `memo` skipped the re-parse is
  // invisible from here, because React writes nothing to the DOM for markup that
  // came out identical either way. That saving is real and this test is not its
  // witness.
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

    // The same DOM node, not an equal one: a settled block is immutable, so
    // re-parsing the answer must not cost the reader the block they are reading.
    expect(container.querySelector("p")).toBe(first);
    expect([...container.querySelectorAll("p")].map((p) => p.textContent)).toContain(
      "Second is done.",
    );
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
