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
  modelFamily,
  modelListRows,
} from "../lib/modelGroups";

// Real ids, because the family rule reads their shape: `gemini-2.5-*` are one
// family, `gemini-3.1-*` another. Made-up ids like "g0" would each be their own
// family and the test would prove nothing about grouping.
const google = [
  "gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite",
  "gemini-3.1-pro-preview", "gemini-3.1-flash-lite",
  "gemini-3.5-flash", "gemini-3.5-flash-lite",
  "gemma-4-31b-it",
].map((id) => ({ id, group: "Google" }));
const anthropic = [{ id: "claude-opus-4-8", group: "Anthropic" }];

describe("modelListRows", () => {
  it("folds a long group to three rows plus a count, and leaves a short one alone", () => {
    const rows = modelListRows([...anthropic, ...google], new Set(["Google"]));
    const kinds = rows.map((r) => r.kind);

    // Anthropic is expanded (one model, nothing to fold) and its single family
    // groups nothing, so no family label — heading straight to the model.
    expect(kinds.slice(0, 2)).toEqual(["heading", "option"]);
    expect(rows.filter((r) => r.kind === "option")).toHaveLength(1 + COLLAPSED_ROW_COUNT);
    const more = rows.find((r) => r.kind === "more");
    expect(more).toMatchObject({ key: "Google", hidden: google.length - COLLAPSED_ROW_COUNT });
    // FLAT while FOLDED: a three-row preview interrupted by family labels would
    // spend its three rows on furniture. Sliced from Google's own heading — the
    // expanded Anthropic block above it legitimately carries one.
    const googleAt = rows.findIndex((r) => r.kind === "heading" && r.key === "Google");
    expect(kinds.slice(googleAt)).not.toContain("family");
  });

  it("labels families once an expanded company shows them", () => {
    // The ask: nineteen Gemini ids in one column is a wall; the same nineteen
    // under "Gemini 2.5", "Gemini 3.1", "Gemma 4" is a list somebody can scan.
    const rows = modelListRows(google, new Set());
    const families = rows.flatMap((r) => (r.kind === "family" ? [r.family] : []));
    expect(families).toEqual(["Gemini 2.5", "Gemini 3.1", "Gemini 3.5", "Gemma 4"]);
    // Every model still drawn — families organise, they never filter.
    expect(rows.filter((r) => r.kind === "option")).toHaveLength(google.length);
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
      (o) => o.id === "gemma-4-31b-it",
    );
    expect(collapsed.has("Google")).toBe(false);
  });

  it("still folds when the active model is already among the visible three", () => {
    // Opening the whole group would be pointless here: the row is on screen
    // either way, and eight rows cost more than they inform.
    const collapsed = initialCollapsedGroups(
      [...anthropic, ...google],
      (o) => o.id === "gemini-2.5-flash",
    );
    expect(collapsed.has("Google")).toBe(true);
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

describe("families only label what they group", () => {
  it("stays silent when every family holds exactly one model", () => {
    // Opus, Sonnet and Haiku are three families of one. A label above each heads
    // a run of one and says nothing the model's own name does not — the same
    // furniture problem that ruled out folding at family level, one scale down.
    const rows = modelListRows(
      ["claude-opus-4-8", "claude-sonnet-5", "claude-haiku-4-5"].map((id) => ({
        id,
        group: "Anthropic",
      })),
      new Set(),
    );
    expect(rows.filter((r) => r.kind === "family")).toHaveLength(0);
    expect(rows.filter((r) => r.kind === "option")).toHaveLength(3);
  });

  it("labels as soon as one family holds more than one", () => {
    // A single grouping family is enough: once any label covers two rows, the
    // labels are describing structure rather than restating names.
    const rows = modelListRows(
      ["gemini-2.5-pro", "gemini-2.5-flash", "gemma-4-31b-it"].map((id) => ({
        id,
        group: "Google",
      })),
      new Set(),
    );
    expect(rows.flatMap((r) => (r.kind === "family" ? [r.family] : []))).toEqual([
      "Gemini 2.5",
      "Gemma 4",
    ]);
  });
});
