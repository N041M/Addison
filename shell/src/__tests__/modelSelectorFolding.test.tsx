// The composer menu's folder tree, asserted against the RENDERED rows.
//
// Two things this file exists for. The first is that "what does the menu
// actually draw?" was twice answered by reading the library instead of the
// component, and twice wrong — the grouping was covered at the library level and
// the library was right both times, while the panel on screen was not. So these
// tests render the real component and look.
//
// The second is the interaction model itself (owner decision 2026-08-07): a two
// level folder — company → family → model — where opening one folder closes its
// siblings, and a closed folder is a single row. A listbox cannot say "folder",
// so the list is a tree, and the keyboard is the WAI-ARIA tree keyboard. The
// invariant underneath all of it: `aria-activedescendant` must always name a row
// that is in the document, and an accordion can close over the cursor from three
// directions.

import { describe, it, expect, vi, beforeAll, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, within } from "@testing-library/react";
import { ModelSelector } from "../components/ModelSelector";
import type { CloudModel } from "../types/ui";

// jsdom has no layout, so the tree's scroll-into-view is a no-op here.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(cleanup);

const from = (provider: string, providerLabel: string) => (ids: string[]): CloudModel[] =>
  ids.map((id) => ({ id, label: id, effortLevels: [], default: false, provider, providerLabel }));
const google = from("google", "Google");
const anthropic = from("anthropic", "Anthropic");

// Anthropic's two are two families of one, so that company has no family level;
// Google's six group into four families, so it does.
const CATALOG: CloudModel[] = [
  ...anthropic(["claude-opus-4-8", "claude-haiku-4-5"]),
  ...google([
    "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash",
    "gemini-2.0-flash-lite", "gemini-3.1-pro-preview", "gemini-3.5-flash",
  ]),
];

function openMenu(models: CloudModel[], selected: string) {
  render(
    <ModelSelector
      roles={[]}
      cloudModels={models}
      selectedRole="primary"
      selectedCloudModel={selected}
      onSelectModel={vi.fn()}
      onSelectEffort={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: /^Model:/ }));
  return screen.getByRole("tree");
}

/** Rows that open and close, and rows that pick a model. */
const folderRows = () =>
  screen.queryAllByRole("treeitem").filter((r) => r.hasAttribute("aria-expanded"));
const modelRows = () =>
  screen.queryAllByRole("treeitem").filter((r) => r.hasAttribute("aria-selected"));
/** Where the keyboard says it is — resolved through the document, which is the
 * only way to catch the failure this is here to catch. */
const cursorRow = (tree: HTMLElement) =>
  document.getElementById(tree.getAttribute("aria-activedescendant") ?? "");

describe("the composer menu as folders", () => {
  it("opens on the path to the model in effect, with everything else shut", () => {
    const tree = openMenu(CATALOG, "claude-opus-4-8");

    expect(Array.from(tree.children).map((c) => c.textContent ?? "")).toEqual([
      expect.stringContaining("Anthropic"),
      expect.stringContaining("claude-opus-4-8"),
      expect.stringContaining("claude-haiku-4-5"),
      expect.stringContaining("Google"),
    ]);
    // Six Google models, one row. No preview, no "N more…".
    expect(screen.queryByText(/more…/)).toBeNull();
    expect(screen.queryByText("gemini-2.5-pro")).toBeNull();
  });

  it("hides every other company's models when one opens", () => {
    // THE ASK: "clicking on google should hide all others."
    const tree = openMenu(CATALOG, "claude-opus-4-8");
    fireEvent.click(screen.getByText("Google"));

    expect(modelRows()).toHaveLength(0);
    // Scoped to the tree: the composer label names the active model too.
    expect(within(tree).queryByText("claude-opus-4-8")).toBeNull();
    expect(Array.from(tree.children).map((c) => c.textContent ?? "")).toEqual([
      expect.stringContaining("Anthropic"),
      expect.stringContaining("Google"),
      expect.stringContaining("Gemini 2.5"),
      expect.stringContaining("Gemini 2.0"),
      expect.stringContaining("Gemini 3.1"),
      expect.stringContaining("Gemini 3.5"),
    ]);
  });

  it("opens one family at a time inside the open company", () => {
    openMenu(CATALOG, "claude-opus-4-8");
    fireEvent.click(screen.getByText("Google"));

    fireEvent.click(screen.getByText("Gemini 2.5"));
    expect(modelRows().map((r) => r.textContent)).toEqual([
      expect.stringContaining("gemini-2.5-pro"),
      expect.stringContaining("gemini-2.5-flash"),
    ]);

    fireEvent.click(screen.getByText("Gemini 2.0"));
    expect(modelRows().map((r) => r.textContent)).toEqual([
      expect.stringContaining("gemini-2.0-flash"),
      expect.stringContaining("gemini-2.0-flash-lite"),
    ]);

    // And clicking the open one shuts it: a legal, fully-closed level.
    fireEvent.click(screen.getByText("Gemini 2.0"));
    expect(modelRows()).toHaveLength(0);
  });

  it("names the company once, on its folder, and never again on a row", () => {
    // Attribution used to be a per-row suffix ("gemini-2.5-pro — Google"), drawn
    // only when more than one provider was connected. With two connected that is
    // the company repeated on every row, in the width the model's name needs.
    openMenu(CATALOG, "gemini-2.5-pro");
    for (const company of ["Google", "Anthropic"]) {
      expect(screen.getAllByText(company)).toHaveLength(1);
    }
    for (const row of modelRows()) expect(row.textContent).not.toMatch(/—/);
    // The same honesty the Settings popup carries: which models a key may call
    // is unknowable until it calls one.
    expect(screen.getByText(/not every model works with every key/)).toBeTruthy();
  });

  it("announces the tree it draws", () => {
    // aria-level is what carries the depth: the rows are siblings in the DOM.
    openMenu(CATALOG, "gemini-2.0-flash-lite");
    expect(folderRows().map((r) => r.getAttribute("aria-level"))).toEqual([
      "1", // Anthropic
      "1", // Google
      "2", // Gemini 2.5
      "2", // Gemini 2.0, open
      "2", // Gemini 3.1
      "2", // Gemini 3.5
    ]);
    expect(modelRows().every((r) => r.getAttribute("aria-level") === "3")).toBe(true);
  });
});

describe("the tree keyboard", () => {
  it("walks the rows that are drawn, and only those", () => {
    const tree = openMenu(CATALOG, "claude-opus-4-8");
    expect(cursorRow(tree)?.textContent).toContain("claude-opus-4-8");

    fireEvent.keyDown(tree, { key: "ArrowDown" });
    expect(cursorRow(tree)?.textContent).toContain("claude-haiku-4-5");
    fireEvent.keyDown(tree, { key: "ArrowDown" });
    // Google's six models are folded away, so the next row is its folder.
    expect(cursorRow(tree)?.textContent).toContain("Google");
    fireEvent.keyDown(tree, { key: "ArrowDown" });
    expect(cursorRow(tree)?.textContent).toContain("Anthropic");
    fireEvent.keyDown(tree, { key: "End" });
    expect(cursorRow(tree)?.textContent).toContain("Google");
    fireEvent.keyDown(tree, { key: "Home" });
    expect(cursorRow(tree)?.textContent).toContain("Anthropic");
  });

  it("opens a folder with Right and closes it with Left", () => {
    const tree = openMenu(CATALOG, "claude-opus-4-8");
    fireEvent.keyDown(tree, { key: "End" }); // Google's folder

    fireEvent.keyDown(tree, { key: "ArrowRight" });
    expect(screen.getByText("Gemini 2.5")).toBeTruthy();
    expect(cursorRow(tree)?.textContent).toContain("Google");

    fireEvent.keyDown(tree, { key: "ArrowLeft" });
    expect(screen.queryByText("Gemini 2.5")).toBeNull();
    expect(cursorRow(tree)?.textContent).toContain("Google");
  });

  it("climbs to the folder a model sits in", () => {
    const tree = openMenu(CATALOG, "gemini-2.0-flash-lite");
    expect(cursorRow(tree)?.textContent).toContain("gemini-2.0-flash-lite");

    fireEvent.keyDown(tree, { key: "ArrowLeft" }); // to its family
    expect(cursorRow(tree)?.textContent).toContain("Gemini 2.0");
    expect(modelRows()).toHaveLength(2); // climbing closes nothing

    fireEvent.keyDown(tree, { key: "ArrowLeft" }); // now it closes
    expect(modelRows()).toHaveLength(0);
    fireEvent.keyDown(tree, { key: "ArrowLeft" }); // and climbs again
    expect(cursorRow(tree)?.textContent).toContain("Google");
  });

  it("picks a model with Enter and toggles a folder with it", () => {
    const onSelectModel = vi.fn();
    render(
      <ModelSelector
        roles={[]}
        cloudModels={CATALOG}
        selectedRole="primary"
        selectedCloudModel="claude-opus-4-8"
        onSelectModel={onSelectModel}
        onSelectEffort={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /^Model:/ }));
    const tree = screen.getByRole("tree");

    fireEvent.keyDown(tree, { key: "End" }); // Google's folder
    fireEvent.keyDown(tree, { key: "Enter" });
    expect(screen.getByText("Gemini 2.5")).toBeTruthy();
    expect(onSelectModel).not.toHaveBeenCalled();

    fireEvent.keyDown(tree, { key: "ArrowDown" }); // onto the family
    fireEvent.keyDown(tree, { key: "Enter" }); // open it
    fireEvent.keyDown(tree, { key: "ArrowDown" }); // onto its first model
    fireEvent.keyDown(tree, { key: "Enter" });
    expect(onSelectModel).toHaveBeenCalledWith("primary", "gemini-2.5-pro");
    // Picking closes the menu, as it always has.
    expect(screen.queryByRole("tree")).toBeNull();
  });
});

describe("the cursor never names a row that is not drawn", () => {
  // `aria-activedescendant` pointing at an element that is not in the document
  // tells a screen reader the cursor is on nothing. An accordion can take the
  // active row away from underneath in three ways: the row's own folder closes,
  // a folder above it closes, or a SIBLING company opens and closes the one the
  // cursor was standing in. The last is only reachable with a mouse — the
  // keyboard has to walk to the other company first.
  it("survives a sibling company opening under the mouse", () => {
    const tree = openMenu(CATALOG, "gemini-2.0-flash-lite");
    expect(cursorRow(tree)?.textContent).toContain("gemini-2.0-flash-lite");

    fireEvent.click(screen.getByText("Anthropic"));

    expect(within(tree).queryByText("gemini-2.0-flash-lite")).toBeNull();
    expect(cursorRow(tree), "the cursor must still name a rendered row").toBeTruthy();
    expect(cursorRow(tree)?.textContent).toContain("Anthropic");
  });

  it("survives its own folder closing under the keyboard", () => {
    const tree = openMenu(CATALOG, "gemini-2.0-flash-lite");
    fireEvent.keyDown(tree, { key: "ArrowUp" }); // onto the sibling model
    expect(cursorRow(tree)?.textContent).toContain("gemini-2.0-flash");

    // Climb to the family, then close it: the row the cursor was on is gone.
    fireEvent.keyDown(tree, { key: "ArrowLeft" });
    fireEvent.keyDown(tree, { key: "ArrowLeft" });
    expect(modelRows()).toHaveLength(0);
    expect(cursorRow(tree), "the cursor must still name a rendered row").toBeTruthy();
    expect(cursorRow(tree)?.textContent).toContain("Gemini 2.0");
  });
});

describe("a refused model", () => {
  // Core-observed, never inferred (a `model_gone` row in `provider_attempts`):
  // Google lists `gemini-2.5-flash`, advertises the right method, then refuses it
  // as "no longer available to new users". Marked and sunk, never removed — a
  // model that quietly vanished would be a worse mystery than one visibly struck
  // through, and the person may know something the log does not.
  const withRefusal: CloudModel[] = google(["gemini-2.5-pro", "gemini-2.5-flash"]).map((m) =>
    m.id === "gemini-2.5-flash" ? { ...m, unavailable: "no longer available to new users" } : m,
  );

  it("is dimmed, struck through, and says so in its note", () => {
    openMenu(withRefusal, "gemini-2.5-pro");
    const row = screen.getByRole("treeitem", { name: /gemini-2\.5-flash/ });
    expect(row.querySelector(".line-through")?.textContent).toBe("gemini-2.5-flash");
    expect(row.textContent).toContain("unavailable");
    // The working sibling is untouched — only the core's own report dims a row.
    const ok = screen.getByRole("treeitem", { name: /gemini-2\.5-pro/ });
    expect(ok.querySelector(".line-through")).toBeNull();
    expect(ok.textContent).toContain("quality");
  });

  it("still reads as struck through when it is the model in effect", () => {
    openMenu(withRefusal, "gemini-2.5-flash");
    const row = screen.getByRole("treeitem", { selected: true });
    expect(row.textContent).toContain("gemini-2.5-flash");
    expect(row.querySelector(".line-through")).toBeTruthy();
    expect(row.textContent).toContain("unavailable ✓");
  });
});

describe("a catalogue whose companies and families interleave", () => {
  // A provider's list is not sorted by family and a caller may hand a company
  // over in two pieces. Reading runs drew one family as two rows under one React
  // key, which left family labels behind over nothing after a fold (observed
  // 2026-08-07, with a console full of "Encountered two children with the same
  // key"). Bucketing makes that unrepresentable; the spy stays as the sentinel.
  const interleaved: CloudModel[] = [
    ...google(["gemini-2.5-pro", "gemini-2.0-flash", "gemini-3.1-pro-preview"]),
    ...anthropic(["claude-opus-4-8"]),
    ...google(["gemini-2.5-flash-8b", "gemini-3.5-flash"]),
  ];

  it("draws one folder per company and per family, without duplicate keys", () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    openMenu(interleaved, "claude-opus-4-8");
    fireEvent.click(screen.getByText("Google"));

    expect(screen.getAllByText("Google")).toHaveLength(1);
    expect(screen.getAllByText("Gemini 2.5")).toHaveLength(1);
    fireEvent.click(screen.getByText("Gemini 2.5"));
    expect(modelRows()).toHaveLength(2);

    const complaints = errors.mock.calls.map((c) => String(c[0]));
    expect(complaints.filter((m) => /same key/i.test(m))).toEqual([]);
    errors.mockRestore();
  });
});
