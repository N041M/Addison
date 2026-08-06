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
import { render, screen, cleanup, within } from "@testing-library/react";
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
