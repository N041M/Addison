// Grouping + collapse for the model lists (lib/modelGroups.ts).
//
// Both panels were designed for about five models. Registration then started
// using the provider's real list instead of two hardcoded ids, and one connected
// Google key contributes twenty-two — so a panel that draws every row is a scroll,
// not a menu. The fold is the sidebar's own pattern from the brief (3 rows +
// "N more…"), reused rather than invented.

import { describe, it, expect } from "vitest";
import {
  COLLAPSED_ROW_COUNT,
  initialCollapsedGroups,
  modelListRows,
} from "../lib/modelGroups";

const google = Array.from({ length: 8 }, (_, i) => ({ id: `g${i}`, group: "Google" }));
const anthropic = [{ id: "a0", group: "Anthropic" }];

describe("modelListRows", () => {
  it("folds a long group to three rows plus a count, and leaves a short one alone", () => {
    const rows = modelListRows([...anthropic, ...google], new Set(["Google"]));
    const kinds = rows.map((r) => r.kind);

    // Anthropic: heading + its single row, no "more" — folding a group smaller
    // than the fold would hide nothing and cost a click.
    expect(kinds.slice(0, 2)).toEqual(["heading", "option"]);
    expect(rows.filter((r) => r.kind === "option")).toHaveLength(1 + COLLAPSED_ROW_COUNT);
    const more = rows.find((r) => r.kind === "more");
    expect(more).toMatchObject({ group: "Google", hidden: 8 - COLLAPSED_ROW_COUNT });
  });

  it("keeps the caller's order and never re-buckets", () => {
    // Interleaved on purpose: a function that grouped by NAME would merge these
    // into two runs and move rows out from under the caller's indices.
    const interleaved = [
      { id: "a", group: "Anthropic" },
      { id: "g", group: "Google" },
      { id: "a2", group: "Anthropic" },
    ];
    const rows = modelListRows(interleaved, new Set());
    const ids = rows.flatMap((r) => (r.kind === "option" ? [r.option.id] : []));
    expect(ids).toEqual(["a", "g", "a2"]);
    expect(rows.filter((r) => r.kind === "heading")).toHaveLength(3);
  });

  it("gives every option its index in the ORIGINAL array", () => {
    // The selector's `activeIndex` and `optionId` mean positions in `options`,
    // so a fold must not renumber them — otherwise expanding a group would move
    // the keyboard's cursor to a different model.
    const rows = modelListRows([...anthropic, ...google], new Set(["Google"]));
    const options = rows.flatMap((r) => (r.kind === "option" ? [r] : []));
    expect(options.map((r) => r.index)).toEqual([0, 1, 2, 3]);
  });

  it("draws ungrouped options with no heading at all", () => {
    const rows = modelListRows([{ id: "x", group: undefined }, { id: "y", group: undefined }], new Set());
    expect(rows.every((r) => r.kind === "option")).toBe(true);
  });
});

describe("initialCollapsedGroups", () => {
  it("folds long groups and leaves short ones open", () => {
    const collapsed = initialCollapsedGroups([...anthropic, ...google], () => false);
    expect(collapsed.has("Google")).toBe(true);
    expect(collapsed.has("Anthropic")).toBe(false);
  });

  it("leaves open the group holding the model that is actually in effect", () => {
    // THE POINT OF THE FUNCTION. Folding by size alone hides the ACTIVE model
    // whenever it sits fourth or lower in its company — so the menu would open
    // saying nothing about what is on, which is the one thing it exists to say.
    const collapsed = initialCollapsedGroups(
      [...anthropic, ...google],
      (o) => o.id === "g6",
    );
    expect(collapsed.has("Google")).toBe(false);
  });

  it("still folds when the active model is already among the visible three", () => {
    // Opening the whole group would be pointless here: the row is on screen
    // either way, and eight rows cost more than they inform.
    const collapsed = initialCollapsedGroups(
      [...anthropic, ...google],
      (o) => o.id === "g1",
    );
    expect(collapsed.has("Google")).toBe(true);
  });
});
