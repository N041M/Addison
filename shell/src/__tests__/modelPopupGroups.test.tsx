// The Settings picker as a folder tree (owner decision 2026-08-07: "a two level
// folder — company - model family - model… Clicking on google should hide all
// others"). Attribution used to be a per-row suffix ("GPT-4.1 — OpenAI") and
// then a heading with a three-row preview under it; with four providers
// connected the first repeated the company on every row and the second was
// twenty-two rows of scroll wearing a lid.
//
// What is asserted here is the ACCORDION and what it hides, because that is the
// part a person feels: one company open, one family inside it, and every other
// company down to a single row.
//
// The second half of the file is about the panel's POSITION, which the folders
// changed: the popup exists to put the selected row under the click point, and
// that used to be arithmetic over the selected row's index. Company and family
// rows sit between the top of the panel and that row, so the arithmetic
// described a layout that is no longer drawn — and the panel, which never
// unmounts, has to re-derive and re-measure rather than trust what it worked out
// the first time it opened.

import { describe, it, expect, afterEach, beforeAll, afterAll, vi } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { ModelPopup, popupTop, type ModelPopupOption } from "../components/ModelPopup";

afterEach(cleanup);

function option(label: string, group: string, selected = false): ModelPopupOption {
  return { key: `${group}:${label}`, id: label, label, group, note: "quality", selected, onPick: vi.fn() };
}

const GOOGLE_IDS = [
  "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite",
  "gemini-3.1-pro-preview", "gemini-3.1-flash-lite",
  "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemma-4-31b-it",
];

/** Rows that open and close. They are the ones carrying `aria-expanded`. */
const folderRows = () =>
  screen.queryAllByRole("treeitem").filter((r) => r.hasAttribute("aria-expanded"));
/** Rows that pick a model. They are the ones carrying `aria-selected`. */
const modelRows = () =>
  screen.queryAllByRole("treeitem").filter((r) => r.hasAttribute("aria-selected"));
const textOf = (rows: HTMLElement[]) => rows.map((r) => r.textContent ?? "");

const OPTIONS: ModelPopupOption[] = [
  option("Claude Opus 4.8", "Anthropic", true),
  option("Claude Haiku 4.5", "Anthropic"),
  option("gemini-3-pro", "Google"),
  option("llama3", "On this computer"),
];

describe("the picker opens as folders", () => {
  it("opens on the path to the model in effect, and nothing else", () => {
    render(<ModelPopup anchor={{ x: 400, y: 300 }} options={OPTIONS} onClose={vi.fn()} />);

    // Anthropic holds the selection, so it is the one company standing open.
    expect(textOf(modelRows())).toEqual([
      expect.stringContaining("Claude Opus 4.8"),
      expect.stringContaining("Claude Haiku 4.5"),
    ]);
    expect(folderRows().map((r) => r.getAttribute("aria-expanded"))).toEqual([
      "true",
      "false",
      "false",
    ]);
  });

  it("prints each company once, in the order the caller passed", () => {
    render(<ModelPopup anchor={{ x: 400, y: 300 }} options={OPTIONS} onClose={vi.fn()} />);

    for (const company of ["Anthropic", "Google", "On this computer"]) {
      expect(screen.getAllByText(company)).toHaveLength(1);
    }
    const text = screen.getByRole("tree").textContent ?? "";
    expect(text.indexOf("Anthropic")).toBeLessThan(text.indexOf("Google"));
    expect(text.indexOf("Google")).toBeLessThan(text.indexOf("On this computer"));
  });

  it("draws no folders at all when the caller supplies no groups", () => {
    // An older caller passing ungrouped options gets exactly what it passed:
    // there is no folder to open, so there is nothing to close either.
    const bare = OPTIONS.map(({ group: _group, ...rest }) => rest);
    render(<ModelPopup anchor={{ x: 400, y: 300 }} options={bare} onClose={vi.fn()} />);

    expect(folderRows()).toHaveLength(0);
    expect(modelRows()).toHaveLength(OPTIONS.length);
  });

  it("says a listed model is not a promise", () => {
    // Google lists gemini-2.5-flash advertising the right method and then refuses
    // it — "no longer available to new users". Nothing on the row could have
    // shown that, and which models a key may use is not knowable until one is
    // called, so the honest place for it is one line under the list.
    render(<ModelPopup anchor={{ x: 400, y: 300 }} options={OPTIONS} onClose={vi.fn()} />);
    expect(screen.getByText(/not every model here works with every key/)).toBeTruthy();
  });
});

describe("the accordion", () => {
  const deep: ModelPopupOption[] = [
    option("claude-opus-4-8", "Anthropic", true),
    ...GOOGLE_IDS.map((id) => option(id, "Google")),
    option("llama3", "On this computer"),
  ];

  it("hides every other company's models when one opens", () => {
    // THE ASK: "clicking on google should hide all others".
    render(<ModelPopup anchor={{ x: 400, y: 300 }} options={deep} onClose={vi.fn()} />);
    expect(textOf(modelRows())).toEqual([expect.stringContaining("claude-opus-4-8")]);

    fireEvent.click(screen.getByText("Google"));

    // Anthropic's model is gone and the local one never appeared; the three
    // companies are still three rows, and Google's families are what is drawn.
    expect(modelRows()).toHaveLength(0);
    expect(folderRows().map((r) => r.getAttribute("aria-expanded"))).toEqual([
      "false", // Anthropic
      "true", // Google
      "false", // Gemini 2.5
      "false", // Gemini 3.1
      "false", // Gemini 3.5
      "false", // Gemma 4
      "false", // On this computer
    ]);
  });

  it("a closed company is exactly one row", () => {
    render(<ModelPopup anchor={{ x: 400, y: 300 }} options={deep} onClose={vi.fn()} />);
    // Eight Google models, one row. No preview, no "N more…".
    expect(screen.getAllByText("Google")).toHaveLength(1);
    expect(screen.queryByText(/more…/)).toBeNull();
    expect(screen.queryByText("gemini-2.5-pro")).toBeNull();
  });

  it("opens one family inside the open company, closing its sibling", () => {
    render(<ModelPopup anchor={{ x: 400, y: 300 }} options={deep} onClose={vi.fn()} />);
    fireEvent.click(screen.getByText("Google"));

    fireEvent.click(screen.getByText("Gemini 2.5"));
    expect(textOf(modelRows())).toEqual([
      expect.stringContaining("gemini-2.5-pro"),
      expect.stringContaining("gemini-2.5-flash"),
      expect.stringContaining("gemini-2.5-flash-lite"),
    ]);

    fireEvent.click(screen.getByText("Gemini 3.1"));
    expect(textOf(modelRows())).toEqual([
      expect.stringContaining("gemini-3.1-pro-preview"),
      expect.stringContaining("gemini-3.1-flash-lite"),
    ]);
  });

  it("closes a folder you click while it is open", () => {
    // Legal, and the only way to a fully-closed tree.
    render(<ModelPopup anchor={{ x: 400, y: 300 }} options={deep} onClose={vi.fn()} />);
    fireEvent.click(screen.getByText("Google"));
    fireEvent.click(screen.getByText("Gemini 2.5"));
    expect(modelRows()).toHaveLength(3);

    fireEvent.click(screen.getByText("Gemini 2.5"));
    expect(modelRows()).toHaveLength(0);

    fireEvent.click(screen.getByText("Google"));
    expect(folderRows()).toHaveLength(3);
    expect(folderRows().every((r) => r.getAttribute("aria-expanded") === "false")).toBe(true);
  });

  it("still picks a model, and the row says which one is in effect", () => {
    const onPick = vi.fn();
    const picked = [
      { ...option("claude-opus-4-8", "Anthropic", true), onPick },
      option("llama3", "On this computer"),
    ];
    render(<ModelPopup anchor={{ x: 400, y: 300 }} options={picked} onClose={vi.fn()} />);
    const row = screen.getByRole("treeitem", { selected: true });
    fireEvent.click(row);
    expect(onPick).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// A model the provider has actually refused
// ---------------------------------------------------------------------------
// Core-observed, never inferred: a `model_gone` row in `provider_attempts`, from
// Google listing `gemini-2.5-flash`, advertising the right method, and then
// refusing it as "no longer available to new users". MARKED, NEVER REMOVED — a
// refusal could have been a bad afternoon, and a model that quietly vanished
// would be a worse mystery than one visibly struck through. So it is still a row
// you can pick, inside its folder like any other.
describe("a refused model", () => {
  const refused = (label: string, selected = false): ModelPopupOption => ({
    ...option(label, "Google", selected),
    note: "unavailable",
    unavailable: true,
  });

  it("is dimmed and struck through, and still picks", () => {
    const options = [option("gemini-2.5-pro", "Google", true), refused("gemini-2.5-flash")];
    render(<ModelPopup anchor={{ x: 400, y: 300 }} options={options} onClose={vi.fn()} />);

    const row = screen.getByRole("treeitem", { name: /gemini-2\.5-flash/ });
    expect(row.className).toContain("text-disabled");
    expect(row.querySelector(".line-through")?.textContent).toBe("gemini-2.5-flash");
    expect(row.textContent).toContain("unavailable");

    fireEvent.click(row);
    expect(options[1].onPick).toHaveBeenCalledTimes(1);
  });

  it("never wears the accent rail, even as the model in effect", () => {
    // The rail means "this is what answers". A row that is struck through
    // because the provider refused it is the one row that must not claim it.
    const options = [option("gemini-2.5-pro", "Google"), refused("gemini-2.5-flash", true)];
    render(<ModelPopup anchor={{ x: 400, y: 300 }} options={options} onClose={vi.fn()} />);

    const row = screen.getByRole("treeitem", { selected: true });
    expect(row.textContent).toContain("gemini-2.5-flash");
    expect(row.className).toContain("border-l-transparent");
    expect(row.className).not.toContain("border-l-accent");
  });
});

// ---------------------------------------------------------------------------
// Interleaving: a catalogue is not sorted, and a folder is not a run
// ---------------------------------------------------------------------------
// Observed in the running app on 2026-08-07: family labels drawn twice, and left
// behind over nothing after a fold, with a console full of "Encountered two
// children with the same key". Reading the caller's runs meant one family could
// head two rows. Bucketing makes that unrepresentable — the spy stays as the
// sentinel.
describe("a catalogue whose companies and families interleave", () => {
  const interleaved: ModelPopupOption[] = [
    ...["gemini-2.5-pro", "gemini-2.0-flash", "gemini-3.1-pro-preview", "gemini-2.5-flash-8b"].map(
      (id) => option(id, "Google"),
    ),
    option("claude-opus-4-8", "Anthropic", true),
    ...["gemini-3.5-flash", "gemini-2.5-flash"].map((id) => option(id, "Google")),
  ];

  it("draws one folder per company and per family, without duplicate keys", () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    render(<ModelPopup anchor={{ x: 400, y: 300 }} options={interleaved} onClose={vi.fn()} />);
    fireEvent.click(screen.getByText("Google"));

    expect(screen.getAllByText("Google")).toHaveLength(1);
    expect(screen.getAllByText("Gemini 2.5")).toHaveLength(1);
    // Six models across two runs, one folder: 2.5 holds three of them.
    fireEvent.click(screen.getByText("Gemini 2.5"));
    expect(modelRows()).toHaveLength(3);

    const complaints = errors.mock.calls.map((c) => String(c[0]));
    expect(complaints.filter((m) => /same key/i.test(m))).toEqual([]);
    errors.mockRestore();
  });
});

// ---------------------------------------------------------------------------
// Re-deriving: this panel never unmounts
// ---------------------------------------------------------------------------
// App.tsx keeps the popup mounted for the life of the app — it holds the last
// anchor so the panel can fade out where it opened, and only `open` goes false.
// Folder state derived once, at mount, would therefore describe the catalogue as
// it stood at the FIRST open, and the promise that the panel opens on the model
// in effect would hold exactly once.
describe("the popup's folders, across openings", () => {
  const anthropicSelected: ModelPopupOption[] = [
    option("claude-opus-4-8", "Anthropic", true),
    ...GOOGLE_IDS.map((id) => option(id, "Google")),
  ];
  const googleSelected: ModelPopupOption[] = [
    option("claude-opus-4-8", "Anthropic"),
    ...GOOGLE_IDS.map((id, i) => option(id, "Google", i === 6)),
  ];

  function reopen(rerender: (ui: React.ReactElement) => void, options: ModelPopupOption[]) {
    rerender(
      <ModelPopup anchor={{ x: 400, y: 300 }} open={false} options={options} onClose={vi.fn()} />,
    );
    rerender(<ModelPopup anchor={{ x: 400, y: 300 }} open options={options} onClose={vi.fn()} />);
  }

  it("re-derives the open path every time it opens", () => {
    const { rerender } = render(
      <ModelPopup anchor={{ x: 400, y: 300 }} open options={anthropicSelected} onClose={vi.fn()} />,
    );
    // Somebody goes looking through Google, then closes the popup.
    fireEvent.click(screen.getByText("Google"));
    expect(screen.getByText("Gemini 2.5")).toBeTruthy();

    reopen(rerender, anthropicSelected);

    // It opens where the model in effect is, not where they were browsing.
    expect(textOf(modelRows())).toEqual([expect.stringContaining("claude-opus-4-8")]);
    expect(screen.queryByText("Gemini 2.5")).toBeNull();
  });

  it("follows a selection that moved while it was closed", () => {
    const { rerender } = render(
      <ModelPopup anchor={{ x: 400, y: 300 }} open options={anthropicSelected} onClose={vi.fn()} />,
    );
    reopen(rerender, googleSelected);

    // Google's "Gemini 3.5" now holds the model in effect: both open, and the
    // row is on screen.
    expect(screen.getByRole("treeitem", { selected: true }).textContent).toContain(
      "gemini-3.5-flash-lite",
    );
    expect(screen.getByRole("treeitem", { name: /^Gemini 3\.5/ })).toHaveProperty(
      "ariaExpanded",
      "true",
    );
  });

  it("leaves the folders alone while the panel stays open", () => {
    // Deriving is for OPENING. A person who opened Google and then moved the
    // mouse — which re-renders the parent — has not asked for it to shut again.
    const { rerender } = render(
      <ModelPopup anchor={{ x: 400, y: 300 }} open options={anthropicSelected} onClose={vi.fn()} />,
    );
    fireEvent.click(screen.getByText("Google"));
    rerender(
      <ModelPopup anchor={{ x: 400, y: 300 }} open options={anthropicSelected} onClose={vi.fn()} />,
    );
    expect(screen.getByRole("treeitem", { name: /^Google/ })).toHaveProperty(
      "ariaExpanded",
      "true",
    );
  });
});

// ---------------------------------------------------------------------------
// Where the panel lands
// ---------------------------------------------------------------------------
describe("popupTop", () => {
  // The arithmetic on its own, because jsdom lays nothing out: the component
  // tests below feed it a synthetic layout, and this one feeds it numbers.
  it("puts the selected row's centre on the click point", () => {
    expect(popupTop({ anchorY: 400, selectedCenter: 100, panelHeight: 300, viewportHeight: 800 }))
      .toBe(300);
  });

  it("keeps the panel inside the top edge", () => {
    // A selection far down the list under a trigger near the top of the window
    // would otherwise start the panel above the screen.
    expect(popupTop({ anchorY: 40, selectedCenter: 300, panelHeight: 400, viewportHeight: 800 }))
      .toBe(12);
  });

  it("keeps the panel inside the bottom edge", () => {
    // Trigger near the bottom, selection at the very top of a tall panel.
    expect(popupTop({ anchorY: 780, selectedCenter: 20, panelHeight: 300, viewportHeight: 800 }))
      .toBe(488);
  });

  it("pins to the margin when the panel is taller than the window", () => {
    // Twenty-two models open is ~900px. No top position keeps the bottom on
    // screen, so the panel starts at the margin and scrolls inside itself.
    expect(popupTop({ anchorY: 400, selectedCenter: 200, panelHeight: 1200, viewportHeight: 800 }))
      .toBe(12);
  });
});

describe("the popup's position, over a real layout", () => {
  // jsdom performs no layout, so every element reports 0 for offsetTop and
  // offsetHeight and the panel would measure every row as sitting at its top.
  // Stack the panel's children ROW_H apart instead — that, the panel's own
  // height, and how much of it is visible is the whole of what the positioning
  // reads.
  const ROW_H = 40;
  // How much of the panel a test lets be visible at once. Big enough to show
  // everything unless a test says otherwise.
  let scrollportHeight = 10_000;
  let windowHeight = 768;
  // jsdom's own scrollTop setter is a no-op (nothing scrolls what was never laid
  // out), so it has to hold a value here for the panel's own write to be legible.
  const scrollTops = new WeakMap<Element, number>();
  const restore: Array<[object, string, PropertyDescriptor | undefined]> = [];

  function stub(target: object, name: string, descriptor: PropertyDescriptor) {
    restore.push([target, name, Object.getOwnPropertyDescriptor(target, name)]);
    Object.defineProperty(target, name, { configurable: true, ...descriptor });
  }

  const isPanel = (el: Element) => el.getAttribute("role") === "tree";

  beforeAll(() => {
    stub(HTMLElement.prototype, "offsetTop", {
      get(this: HTMLElement) {
        const parent = this.parentElement;
        return parent ? Array.prototype.indexOf.call(parent.children, this) * ROW_H : 0;
      },
    });
    stub(HTMLElement.prototype, "offsetHeight", {
      get(this: HTMLElement) {
        return isPanel(this) ? this.children.length * ROW_H : ROW_H;
      },
    });
    stub(Element.prototype, "scrollHeight", {
      get(this: Element) {
        return isPanel(this) ? this.children.length * ROW_H : ROW_H;
      },
    });
    stub(Element.prototype, "clientHeight", {
      get(this: Element) {
        return isPanel(this) ? scrollportHeight : ROW_H;
      },
    });
    stub(Element.prototype, "scrollTop", {
      get(this: Element) {
        return scrollTops.get(this) ?? 0;
      },
      set(this: Element, value: number) {
        scrollTops.set(this, value);
      },
    });
    stub(window, "innerHeight", { get: () => windowHeight });
  });

  afterAll(() => {
    for (const [target, name, descriptor] of restore) {
      if (descriptor) Object.defineProperty(target, name, descriptor);
    }
  });

  afterEach(() => {
    scrollportHeight = 10_000;
    windowHeight = 768;
  });

  function viewportHeight(px: number) {
    windowHeight = px;
  }

  const deepInGoogle: ModelPopupOption[] = [
    option("claude-opus-4-8", "Anthropic"),
    // 7th of eight, so its company and its family open with the panel — and with
    // the folders in front of it the row is drawn far below where its index says.
    ...GOOGLE_IDS.map((id, i) => option(id, "Google", i === 6)),
  ];

  it("measures where the selected row is DRAWN, not where its index would put it", () => {
    viewportHeight(2000);
    render(<ModelPopup anchor={{ x: 400, y: 700 }} options={deepInGoogle} onClose={vi.fn()} />);

    // Drawn: Anthropic, Google, three closed families, the open "Gemini 3.5"
    // with its two models — the selected row is the seventh child, centre 260.
    // The old arithmetic (14 + index × 29, with the row at index 7) said 217,
    // which is a row it never sits on.
    expect(screen.getByRole("tree").style.top).toBe("440px");
  });

  it("does not slide out from under the hand when a folder opens", () => {
    // Anchored ONCE per open. Opening a company moves every row below it;
    // re-centring on the selection then would jump the panel while the pointer
    // is still on the row that was just clicked.
    viewportHeight(2000);
    const options = [
      ...GOOGLE_IDS.map((id) => option(id, "Google")),
      option("llama3", "On this computer", true),
    ];
    render(<ModelPopup anchor={{ x: 400, y: 700 }} options={options} onClose={vi.fn()} />);
    const panel = screen.getByRole("tree");
    const before = panel.style.top;

    fireEvent.click(screen.getByText("Google"));
    expect(screen.getByText("Gemini 2.5")).toBeTruthy();
    expect(panel.style.top).toBe(before);
  });

  it("re-clamps to the bottom edge as opening folders makes it taller", () => {
    // The height used to be keyed on the option COUNT, which a fold does not
    // change: opening a folder grew the panel while the clamp went on believing
    // it was short, and the bottom of the list ran off the window.
    viewportHeight(600);
    const options = [
      option("claude-opus-4-8", "Anthropic", true),
      ...GOOGLE_IDS.map((id) => option(id, "Google")),
    ];
    render(<ModelPopup anchor={{ x: 400, y: 300 }} options={options} onClose={vi.fn()} />);
    const panel = screen.getByRole("tree");
    // Four rows drawn (160px), so nothing is clamped yet.
    expect(panel.style.top).toBe("240px");

    fireEvent.click(screen.getByText("Google")); // seven rows: still room
    expect(panel.style.top).toBe("240px");
    fireEvent.click(screen.getByText("Gemini 2.5")); // ten rows: the clamp bites
    expect(panel.style.top).toBe("188px");
  });

  // A panel pinned to the margin and scrolling inside itself is only half an
  // answer: observed live with 22 models and the eighteenth selected, the popup
  // opened on rows one to six. The menu exists to say what is in effect, so the
  // row that says it has to be inside the scrollport when it opens.
  it("opens with the selected row inside its own scrollport", () => {
    viewportHeight(768);
    scrollportHeight = 200; // five rows of a nine-row panel
    render(<ModelPopup anchor={{ x: 400, y: 400 }} options={deepInGoogle} onClose={vi.fn()} />);

    const panel = screen.getByRole("tree");
    const row = screen.getByRole("treeitem", { selected: true });
    const centre = row.offsetTop + row.offsetHeight / 2;
    expect(panel.scrollTop).toBeGreaterThan(0);
    expect(centre).toBeGreaterThanOrEqual(panel.scrollTop);
    expect(centre).toBeLessThanOrEqual(panel.scrollTop + panel.clientHeight);
  });

  it("leaves the scroll alone when the whole list fits", () => {
    // Nothing to bring into view, and a panel that scrolled anyway would hide
    // its own first rows for no reason.
    viewportHeight(2000);
    render(<ModelPopup anchor={{ x: 400, y: 400 }} options={deepInGoogle} onClose={vi.fn()} />);
    expect(screen.getByRole("tree").scrollTop).toBe(0);
  });
});
