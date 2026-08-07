// The composer menu's folded shape, asserted against the RENDERED rows.
//
// Reported twice from the running app: family labels showing inside a folded
// company, and a family drawn twice. Neither was in the code — both were a
// half-finished revision the dev server had hot-reloaded off disk. But the reason
// it took two rounds to establish that is that nothing here rendered this
// component and looked; the grouping was covered at the library level, and the
// library was right both times.
//
// So this test exists to answer "what does the menu actually draw?" without a
// rebuild, which is the question that cost the round trips.

import { describe, it, expect, vi, beforeAll, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { ModelSelector } from "../components/ModelSelector";
import type { CloudModel } from "../types/ui";

// jsdom has no layout, so the list's scroll-into-view is a no-op here.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(cleanup);

const google = (ids: string[]): CloudModel[] =>
  ids.map((id) => ({
    id,
    label: id,
    effortLevels: [],
    default: false,
    provider: "google",
    providerLabel: "Google",
  }));

const MANY = google([
  "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash",
  "gemini-2.0-flash-lite", "gemini-3.1-pro-preview", "gemini-3.5-flash",
]);

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
  fireEvent.click(screen.getByRole("button", { name: /model/i }));
  return screen.getByRole("listbox");
}

describe("composer menu, folded", () => {
  it("draws the company, three models and the rest — and NO family labels", () => {
    const box = openMenu(MANY, "gemini-2.5-pro");
    const rows = Array.from(box.children).map((c) => c.textContent ?? "");

    expect(rows[0]).toContain("Google");
    expect(screen.getAllByRole("option")).toHaveLength(3);
    expect(rows.at(-1)).toContain("3 more…");
    // THE REPORTED ARTIFACT. A folded company is flat: a three-row preview
    // interrupted by family labels spends its three rows on furniture.
    for (const row of rows) expect(row).not.toMatch(/Gemini 2\.5$/);
  });

  it("draws each family exactly once when expanded", () => {
    const box = openMenu(MANY, "gemini-2.5-pro");
    fireEvent.click(screen.getByRole("button", { name: /3 more…/ }));

    const labels = Array.from(box.children)
      .map((c) => c.textContent ?? "")
      .filter((t) => /^Gemini \d/.test(t));
    // The OTHER reported artifact: "GEMINI 2.5" appearing twice in a row.
    expect(labels).toEqual([...new Set(labels)]);
    expect(labels).toEqual(["Gemini 2.5", "Gemini 2.0", "Gemini 3.1", "Gemini 3.5"]);
    expect(screen.getAllByRole("option")).toHaveLength(MANY.length);
  });
});
