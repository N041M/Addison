// The model lists' folder tree (lib/modelGroups.ts).
//
// Both panels were designed for about five models. Registration then started
// using the provider's real list instead of two hardcoded ids, and one connected
// Google key contributes twenty-two — so a panel that draws every row is a
// scroll, not a menu. The answer is an accordion, by owner decision of
// 2026-08-07: company → family → model, one folder open at each level, and a
// closed folder is one row. What this file pins is that "one open" is a property
// of the state's SHAPE and that the tree is bucketed rather than read off the
// caller's order — two companies called Google is not a folder.

import { describe, it, expect } from "vitest";
import {
  initialFolderState,
  modelFamily,
  modelListRows,
  rowRenderKey,
  toggleCompany,
  toggleFamily,
  type FolderState,
  type ModelListRow,
} from "../lib/modelGroups";

// Real ids, because the family rule reads their shape: `gemini-2.5-*` are one
// family, `gemini-3.1-*` another. Made-up ids like "g0" would each be their own
// family and the test would prove nothing about grouping.
const GOOGLE_IDS = [
  "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite",
  "gemini-3.1-pro-preview", "gemini-3.1-flash-lite",
  "gemini-3.5-flash", "gemini-3.5-flash-lite",
  "gemma-4-31b-it",
];
const google = GOOGLE_IDS.map((id) => ({ id, group: "Google" }));
// Two models, two families: Opus and Haiku are families of one, so this company
// has no family level at all.
const anthropic = [
  { id: "claude-opus-4-8", group: "Anthropic" },
  { id: "claude-haiku-4-5", group: "Anthropic" },
];
const catalogue = [...anthropic, ...google];

// A provider's list is not sorted by family, and the caller may hand a company
// over in two pieces: "Gemini 2.5" heads a run twice and Google arrives twice.
const interleaved = [
  { id: "gemini-2.5-pro", group: "Google" },
  { id: "gemini-2.0-flash", group: "Google" },
  { id: "gemini-3.1-pro-preview", group: "Google" },
  { id: "gemini-2.5-flash-8b", group: "Google" },
  { id: "claude-opus-4-8", group: "Anthropic" },
  { id: "gemini-3.5-flash", group: "Google" },
  { id: "gemini-2.5-flash", group: "Google" },
];

interface Model {
  id: string;
  group?: string;
}
type Row = ModelListRow<Model>;
const kinds = (rows: Row[]) => rows.map((r) => r.kind);
const models = (rows: Row[]) => rows.flatMap((r) => (r.kind === "option" ? [r.option.id] : []));
const folders = (rows: Row[], kind: "company" | "family") =>
  rows.flatMap((r) => (r.kind === kind ? [r.key] : []));

describe("modelListRows", () => {
  it("draws one row per company when everything is closed", () => {
    // THE ASK, in one assertion: "clicking on google should hide all others".
    // Nothing is open here, so ten models are two rows.
    const rows = modelListRows(catalogue, {});
    expect(kinds(rows)).toEqual(["company", "company"]);
    expect(rows.map((r) => (r.kind === "company" ? [r.key, r.total, r.open] : []))).toEqual([
      ["Anthropic", 2, false],
      ["Google", 8, false],
    ]);
  });

  it("opens one company and leaves its families closed", () => {
    const rows = modelListRows(catalogue, { company: "Google" });
    expect(folders(rows, "company")).toEqual(["Anthropic", "Google"]);
    expect(folders(rows, "family")).toEqual([
      "Gemini 2.5",
      "Gemini 3.1",
      "Gemini 3.5",
      "Gemma 4",
    ]);
    // A closed family is a row, not a preview: no models are drawn yet.
    expect(models(rows)).toEqual([]);
  });

  it("opens one family inside the open company", () => {
    const rows = modelListRows(catalogue, { company: "Google", family: "Gemini 3.1" });
    expect(models(rows)).toEqual(["gemini-3.1-pro-preview", "gemini-3.1-flash-lite"]);
    const open = rows.flatMap((r) => (r.kind === "family" && r.open ? [r.key] : []));
    expect(open).toEqual(["Gemini 3.1"]);
    // Company 1, family 2, model 3 — what `aria-level` announces.
    expect(rows.map((r) => r.level)).toEqual([1, 1, 2, 2, 3, 3, 2, 2]);
  });

  it("puts models straight under a company whose families would not group", () => {
    // Opus and Haiku are two families of one. A folder each holds one model and
    // says nothing its own name does not — a drawer per sock.
    const rows = modelListRows(catalogue, { company: "Anthropic" });
    expect(folders(rows, "family")).toEqual([]);
    expect(models(rows)).toEqual(["claude-opus-4-8", "claude-haiku-4-5"]);
    expect(rows.filter((r) => r.kind === "option").map((r) => r.level)).toEqual([2, 2]);
  });

  it("buckets a company that arrives in two runs into ONE folder", () => {
    // The flat list read runs and never re-ordered, so the caller's order was
    // the order on screen. For a folder tree that argument inverts: two rows
    // saying "Google" that open and close separately are not a folder.
    const rows = modelListRows(interleaved, {});
    expect(folders(rows, "company")).toEqual(["Google", "Anthropic"]);
    expect(rows.flatMap((r) => (r.kind === "company" && r.key === "Google" ? [r.total] : []))).toEqual([6]);
  });

  it("buckets a family that appears twice into ONE folder", () => {
    const rows = modelListRows(interleaved, { company: "Google" });
    expect(folders(rows, "family")).toEqual(["Gemini 2.5", "Gemini 2.0", "Gemini 3.1", "Gemini 3.5"]);
    const twoFive = rows.find((r) => r.kind === "family" && r.key === "Gemini 2.5");
    expect(twoFive).toMatchObject({ total: 3 });
  });

  it("gives every model its index in the ORIGINAL array", () => {
    // The popup's anchor row — the one it positions itself by — means a position
    // in the caller's array, so bucketing must not renumber them.
    const rows = modelListRows(interleaved, { company: "Google", family: "Gemini 2.5" });
    const picked = rows.flatMap((r) => (r.kind === "option" ? [[r.option.id, r.index]] : []));
    expect(picked).toEqual([
      ["gemini-2.5-pro", 0],
      ["gemini-2.5-flash-8b", 3],
      ["gemini-2.5-flash", 6],
    ]);
  });

  it("keeps a model the core sank at the end of the run it was sunk in", () => {
    // The core moves a refused model to the end of its company (a `model_gone`
    // row — types/ui.ts owns why it is marked rather than dropped), so bucketing
    // must not float it back up. It cannot land at the end of the COMPANY once
    // there are family folders, but it stays last where it is drawn, which is
    // the same promise one level in.
    const sunk = [
      { id: "gemini-2.5-pro", group: "Google" },
      { id: "gemini-3.1-pro-preview", group: "Google" },
      { id: "gemini-3.1-flash-lite", group: "Google" },
      { id: "gemini-2.5-flash", group: "Google" }, // refused, sunk by the core
    ];
    const rows = modelListRows(sunk, { company: "Google", family: "Gemini 2.5" });
    expect(models(rows)).toEqual(["gemini-2.5-pro", "gemini-2.5-flash"]);
  });

  it("draws ungrouped options with no folder at all", () => {
    // An older caller passing options with no company gets exactly what it
    // passed: there is no folder to open, so there is nothing to close either.
    const rows = modelListRows<Model>([{ id: "x" }, { id: "y" }], {});
    expect(kinds(rows)).toEqual(["option", "option"]);
    expect(rows.every((r) => r.level === 1)).toBe(true);
  });
});

describe("where a row says it sits", () => {
  // `aria-posinset`/`aria-setsize` have to be AUTHORED: both panels draw the tree
  // as flat sibling rows carrying `aria-level`, with no `role="group"` around a
  // folder's contents, and a screen reader can only count an ordinal from a DOM
  // hierarchy that mirrors the tree. Left to it, it counted the whole list —
  // "Google, level 1, 4 of 4" for the second of two companies.
  const ordinals = (rows: Row[]) =>
    rows.map((r) => [
      r.kind === "option" ? r.option.id : r.key,
      `${r.posinset} of ${r.setsize}`,
    ]);

  it("counts a row against its own folder's children, not the list", () => {
    const rows = modelListRows(catalogue, { company: "Anthropic" });
    expect(ordinals(rows)).toEqual([
      ["Anthropic", "1 of 2"],
      // The two models are the company's children, not rows 2 and 3 of four.
      ["claude-opus-4-8", "1 of 2"],
      ["claude-haiku-4-5", "2 of 2"],
      // And the company after them is the SECOND of two companies.
      ["Google", "2 of 2"],
    ]);
  });

  it("counts families against their company and models against their family", () => {
    const rows = modelListRows(catalogue, { company: "Google", family: "Gemini 3.1" });
    expect(ordinals(rows)).toEqual([
      ["Anthropic", "1 of 2"],
      ["Google", "2 of 2"],
      ["Gemini 2.5", "1 of 4"],
      ["Gemini 3.1", "2 of 4"],
      ["gemini-3.1-pro-preview", "1 of 2"],
      ["gemini-3.1-flash-lite", "2 of 2"],
      ["Gemini 3.5", "3 of 4"],
      ["Gemma 4", "4 of 4"],
    ]);
  });

  it("counts an ungrouped option among the rows drawn beside it", () => {
    // A caller that grouped nothing draws options at the root, so the root's set
    // is what is on screen — not what a foldered catalogue would have shown.
    const rows = modelListRows<Model>([{ id: "x" }, { id: "y" }], {});
    expect(ordinals(rows)).toEqual([
      ["x", "1 of 2"],
      ["y", "2 of 2"],
    ]);
  });
});

describe("initialFolderState", () => {
  it("opens the path to the model in effect", () => {
    // THE POINT OF THE FUNCTION. A menu opening on a closed tree says nothing
    // about what is on, which is the one thing it exists to say.
    const state = initialFolderState(catalogue, (o) => o.id === "gemini-3.5-flash-lite");
    expect(state).toEqual({ company: "Google", family: "Gemini 3.5" });
  });

  it("opens only the company when that company has no family level", () => {
    const state = initialFolderState(catalogue, (o) => o.id === "claude-haiku-4-5");
    expect(state).toEqual({ company: "Anthropic", family: undefined });
  });

  it("falls back to the first option when nothing is selected", () => {
    expect(initialFolderState(catalogue, () => false)).toEqual({
      company: "Anthropic",
      family: undefined,
    });
  });

  it("opens nothing at all for a caller with no companies", () => {
    expect(initialFolderState([{ id: "x" }], () => false)).toEqual({});
  });
});

describe("the accordion", () => {
  const selectedIsGemma = (o: { id: string }) => o.id === "gemma-4-31b-it";
  const open: FolderState = { company: "Google", family: "Gemini 2.5" };

  it("closes every other company when one opens", () => {
    // "Clicking on google should hide all others" — and the state cannot say
    // otherwise, which is why it is one field and not a set.
    const next = toggleCompany(open, "Anthropic", catalogue, () => false);
    expect(next).toEqual({ company: "Anthropic", family: undefined });
  });

  it("opens the same tree as the seed would when nothing is selected", () => {
    // The two used to disagree about the no-selection case — the seed fell back
    // to the first option and opened ITS family, the toggle fell back to nothing
    // — so the same company click produced two different trees depending on
    // whether it was the opening or a press.
    const nothingSelected = () => false;
    for (const company of ["Anthropic", "Google"]) {
      const seeded = initialFolderState(catalogue, nothingSelected);
      const toggled = toggleCompany({}, company, catalogue, nothingSelected);
      if (seeded.company === company) expect(toggled).toEqual(seeded);
      else expect(toggled.family).toBeUndefined();
    }
  });

  it("shows you where you are when you open the company you are using", () => {
    const next = toggleCompany({ company: "Anthropic" }, "Google", catalogue, selectedIsGemma);
    expect(next).toEqual({ company: "Google", family: "Gemma 4" });
  });

  it("leaves a hand-opened company's families closed", () => {
    // Opening some other company should not pre-empt what you came to look for.
    const next = toggleCompany({ company: "Anthropic" }, "Google", catalogue, (o) =>
      o.id.startsWith("claude"),
    );
    expect(next).toEqual({ company: "Google", family: undefined });
  });

  it("closes the company you click when it is already open", () => {
    // Legal, and the only way to a fully-closed tree.
    expect(toggleCompany(open, "Google", catalogue, () => false)).toEqual({});
  });

  it("closes a family's siblings, and itself on a second click", () => {
    expect(toggleFamily(open, "Gemini 3.1")).toEqual({
      company: "Google",
      family: "Gemini 3.1",
    });
    expect(toggleFamily(open, "Gemini 2.5")).toEqual({ company: "Google", family: undefined });
  });
});

describe("modelFamily", () => {
  it("finds the grain a person names out loud", () => {
    // The axis DIFFERS per vendor and that is correct, not a compromise:
    // Anthropic distinguishes its models by tier, Google by generation, so the
    // second segment lands on whichever one that vendor actually uses.
    expect(modelFamily("claude-opus-4-8")).toBe("Claude Opus");
    expect(modelFamily("claude-haiku-4-5")).toBe("Claude Haiku");
    expect(modelFamily("gemini-2.5-flash-lite")).toBe("Gemini 2.5");
    expect(modelFamily("gemini-3.1-pro-preview")).toBe("Gemini 3.1");
    expect(modelFamily("gemma-4-31b-it")).toBe("Gemma 4");
    expect(modelFamily("gpt-4o-mini")).toBe("GPT 4o");
  });

  it("handles an id that is not two segments at all", () => {
    // A local model, or anything a provider ships tomorrow. One segment is the
    // family; nothing here may throw on a shape nobody anticipated.
    expect(modelFamily("llama3")).toBe("Llama3");
    expect(modelFamily("o4-mini")).toBe("O4");
    expect(modelFamily("")).toBe("");
  });
});

describe("rowRenderKey", () => {
  // The sentinel for a bug that cost two rounds of "it is broken in the app":
  // duplicate keys made React reconcile two rows against each other and left
  // family labels drawn over nothing (2026-08-07). Bucketing should make that
  // unrepresentable — one row per company, one per family within it — so this
  // asserts the property rather than the old fix.
  const identities = (rows: Row[]) =>
    rows.map((row) => (row.kind === "option" ? `m:${row.index}` : rowRenderKey(row)));

  for (const [name, state] of [
    ["closed", {}],
    ["one company open", { company: "Google" }],
    ["one family open", { company: "Google", family: "Gemini 2.5" }],
  ] as [string, FolderState][]) {
    it(`gives every row its own identity — ${name}`, () => {
      const ids = identities(modelListRows(interleaved, state));
      expect(new Set(ids).size).toBe(ids.length);
    });
  }
});
