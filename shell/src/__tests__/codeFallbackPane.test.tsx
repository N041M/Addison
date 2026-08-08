// The review surface when the editor chunk never arrives.
//
// ITS OWN FILE FOR ONE REASON: `vi.mock` is file-scoped, and every other test of
// this screen mocks a WORKING `../lib/monaco` — so `failed`, the branch the editor's
// own docstring calls "a real state and not a spinner that never stops", was the one
// state nothing in the suite had ever rendered. It was reached in a real build by a
// chunk that 404s after an update and by a webview that refuses the request; it is
// reached here by a module factory that throws, which is how a rejected dynamic
// import is spelled.
//
// WHAT IS ASSERTED IS THE ACCESSIBLE NAME. Both panes accepted an `ariaLabel` and
// dropped it on this path — `PlainText` took only `text` — so a person on a screen
// reader got an unnamed box of code on the one screen in the app whose entire job is
// to be exact about which file is which. The surface's end of that wiring (which
// sentence it composes, and that it reaches the pane) is pinned in
// codeScreen.test.tsx; this is the other end.

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import { CodeDiff, CodeViewer } from "../components/CodeEditor";

// The chunk that cannot be loaded at all.
vi.mock("../lib/monaco", () => {
  throw new Error("the editor chunk could not be loaded");
});

afterEach(cleanup);

describe("the plain-text fallback", () => {
  it("names the file viewer, with the sentence the surface composed", async () => {
    // Kills: taking `ariaLabel` and rendering a bare `<pre>` — which is what shipped.
    // And kills labelling without a role: an element with no role exposes no name,
    // so `aria-label` on a bare `<pre>` is a string nothing reads out.
    render(
      <CodeViewer
        path="/p/src/app.py"
        text="x = 1\n"
        theme="dark"
        ariaLabel="app.py, read only"
      />,
    );
    const pane = await screen.findByRole("region", { name: "app.py, read only" });
    expect(pane.textContent).toContain("x = 1");
  });

  it("names BOTH halves of the diff, and says which is which", async () => {
    // Two stacked panes with no names at all was the shipped state; two stacked panes
    // with the SAME name would be the same bug in a better disguise, because the one
    // thing a person needs from this pane is which side is the earlier version — it is
    // where Revert lands. Kills: dropping the labels, and kills naming both panes with
    // the composed label, which describes the pair.
    render(
      <CodeDiff
        path="/p/src/app.py"
        before="old\n"
        after="new\n"
        theme="dark"
        ariaLabel="app.py, before and after Addison's changes"
      />,
    );
    const before = await screen.findByRole("region", { name: "Before Addison's changes" });
    const after = screen.getByRole("region", { name: "After Addison's changes" });
    expect(before.textContent).toContain("old");
    expect(after.textContent).toContain("new");
    // ...and the file is named once, over the pair.
    expect(
      screen.getByRole("group", { name: "app.py, before and after Addison's changes" }),
    ).toBeTruthy();
  });

  it("shows the text rather than a spinner that never stops", async () => {
    // The reason the fallback exists at all: the text is the thing the person came
    // for. Kills: leaving "opening…" on screen when the load has already failed.
    render(
      <CodeViewer path="/p/a.md" text="hello" theme="dark" ariaLabel="a.md, read only" />,
    );
    await waitFor(() => expect(screen.getByText("hello")).toBeTruthy());
    expect(screen.queryByText("opening…")).toBeNull();
  });
});
