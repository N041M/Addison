// The picker groups models by the company that serves them. Attribution used to
// be a per-row suffix ("GPT-4.1 — OpenAI") shown only when more than one provider
// was connected; with four providers connected that repeated the company on every
// row and ate the width the model's own name needed.
//
// The grouping is DRAWN, never COMPUTED: headings appear wherever the group
// changes, so the caller's order is the order on screen. That matters because
// ModelSelector's keyboard navigation walks its options by index — a popup that
// re-bucketed rows would move the selection out from under the arrow keys.

import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, cleanup, within, fireEvent } from "@testing-library/react";
import { ModelPopup, type ModelPopupOption } from "../components/ModelPopup";

afterEach(cleanup);

function option(label: string, group: string, selected = false): ModelPopupOption {
  return { key: `${group}:${label}`, label, group, note: "quality", selected, onPick: vi.fn() };
}

const OPTIONS: ModelPopupOption[] = [
  option("Claude Opus 4.8", "Anthropic", true),
  option("Claude Haiku 4.5", "Anthropic"),
  option("gemini-3-pro", "Google"),
  option("llama3", "On this computer"),
];

describe("model picker grouping", () => {
  it("prints each company once, in the order the caller passed", () => {
    render(<ModelPopup anchor={{ x: 400, y: 300 }} options={OPTIONS} onClose={vi.fn()} />);

    for (const company of ["Anthropic", "Google", "On this computer"]) {
      expect(screen.getAllByText(company)).toHaveLength(1);
    }
    // Order preserved — the popup draws, it does not re-bucket.
    const text = screen.getByRole("listbox").textContent ?? "";
    expect(text.indexOf("Anthropic")).toBeLessThan(text.indexOf("Google"));
    expect(text.indexOf("Google")).toBeLessThan(text.indexOf("On this computer"));
  });

  it("keeps every model pickable and no heading among them", () => {
    render(<ModelPopup anchor={{ x: 400, y: 300 }} options={OPTIONS} onClose={vi.fn()} />);

    // THE ACCESSIBILITY POINT, and the reason headings are role="presentation".
    // A listbox's children are the things you can choose; a heading announced as
    // an option is a row a screen reader offers and Enter cannot take — and in
    // ModelSelector it would also be an index the arrow keys stop on.
    const options = screen.getAllByRole("option");
    expect(options).toHaveLength(OPTIONS.length);
    for (const [i, expected] of OPTIONS.entries()) {
      expect(within(options[i]).getByText(expected.label)).toBeTruthy();
    }
  });

  it("draws no headings at all when the caller supplies no groups", () => {
    // An older caller passing ungrouped options must render exactly as before,
    // not sprout an empty heading above every row.
    const bare = OPTIONS.map(({ group: _group, ...rest }) => rest);
    render(<ModelPopup anchor={{ x: 400, y: 300 }} options={bare} onClose={vi.fn()} />);

    expect(screen.queryByText("Anthropic")).toBeNull();
    expect(screen.getAllByRole("option")).toHaveLength(OPTIONS.length);
  });
});

describe("model picker folding", () => {
  const many: ModelPopupOption[] = [
    option("Claude Opus 4.8", "Anthropic", true),
    ...Array.from({ length: 8 }, (_, i) => option(`gemini-${i}`, "Google")),
  ];

  it("shows three of eight and offers the rest, then expands on click", () => {
    render(<ModelPopup anchor={{ x: 400, y: 300 }} options={many} onClose={vi.fn()} />);

    // Anthropic (1 row, unfolded) + Google's first three.
    expect(screen.getAllByRole("option")).toHaveLength(4);
    const more = screen.getByRole("button", { name: /5 more/ });

    fireEvent.click(more);
    expect(screen.getAllByRole("option")).toHaveLength(9);
    expect(screen.queryByRole("button", { name: /more/ })).toBeNull();
  });

  it("opens the group holding the model in effect, whatever its position", () => {
    // A menu that folds the ACTIVE model out of sight answers nothing. Selected
    // row is 7th in its company, so the fold must not apply to that group.
    const deep = [
      option("Claude Opus 4.8", "Anthropic"),
      ...Array.from({ length: 8 }, (_, i) => option(`gemini-${i}`, "Google", i === 6)),
    ];
    render(<ModelPopup anchor={{ x: 400, y: 300 }} options={deep} onClose={vi.fn()} />);

    expect(screen.getByText("gemini-6")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /more/ })).toBeNull();
  });

  it("says a listed model is not a promise", () => {
    // Google lists gemini-2.5-flash advertising the right method and then refuses
    // it — "no longer available to new users". Nothing on the row could have
    // shown that, and which models a key may use is not knowable until one is
    // called, so the honest place for it is one line under the list.
    render(<ModelPopup anchor={{ x: 400, y: 300 }} options={many} onClose={vi.fn()} />);
    expect(screen.getByText(/not every model here works with every key/)).toBeTruthy();
  });

  it("keeps the fold controls out of the option roles", () => {
    render(<ModelPopup anchor={{ x: 400, y: 300 }} options={many} onClose={vi.fn()} />);
    for (const opt of screen.getAllByRole("option")) {
      expect(opt.textContent).not.toMatch(/more…|collapse/);
    }
  });
});
