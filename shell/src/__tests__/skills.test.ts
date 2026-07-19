// Contract-drift guard for parseSkillList — the defensive parser between a
// shifted `skill.list` payload and the Settings Skills section. Exercised BOTH
// ways, like parsers.test.ts:
//   (a) a realistic round-trip with the exact `skill.list` shape agent_core
//       sends ({ skills: [{ id, name, instructions, enabled }] }), and
//   (b) the fail-closed fallbacks — null / junk / missing array / a row missing
//       an id or name (dropped) / missing enabled (defaults ON) / a non-object
//       row — which must degrade, never throw.

import { describe, it, expect } from "vitest";
import { parseSkillList } from "../ipc/client";

describe("parseSkillList", () => {
  it("round-trips a realistic skill.list payload (enabled + disabled)", () => {
    const wire = {
      skills: [
        {
          id: "s1",
          name: "Keep it short",
          instructions: "Answer in a sentence or two unless I ask for more.",
          enabled: true,
        },
        {
          id: "s2",
          name: "Amounts in CZK",
          instructions: "Always show money amounts in Czech koruna.",
          enabled: false,
        },
      ],
    };
    expect(parseSkillList(wire)).toEqual([
      {
        id: "s1",
        name: "Keep it short",
        instructions: "Answer in a sentence or two unless I ask for more.",
        enabled: true,
      },
      {
        id: "s2",
        name: "Amounts in CZK",
        instructions: "Always show money amounts in Czech koruna.",
        enabled: false,
      },
    ]);
  });

  it("defaults enabled to true when the field is absent (core defaults enabled=1)", () => {
    expect(parseSkillList({ skills: [{ id: "s1", name: "Note", instructions: "Do the thing." }] })).toEqual([
      { id: "s1", name: "Note", instructions: "Do the thing.", enabled: true },
    ]);
  });

  it("only an explicit false turns a skill off; truthy-but-non-false stays on", () => {
    const parsed = parseSkillList({
      skills: [
        { id: "a", name: "A", enabled: false }, // off
        { id: "b", name: "B", enabled: "yes" }, // non-boolean junk -> stays on
        { id: "c", name: "C", enabled: 0 }, // 0 is not false -> stays on
      ],
    });
    expect(parsed).toEqual([
      { id: "a", name: "A", instructions: "", enabled: false },
      { id: "b", name: "B", instructions: "", enabled: true },
      { id: "c", name: "C", instructions: "", enabled: true },
    ]);
  });

  it("fills an empty-string instructions fallback when absent or wrong-typed", () => {
    const parsed = parseSkillList({
      skills: [
        { id: "s1", name: "No instructions" },
        { id: "s2", name: "Numeric instructions", instructions: 42 },
      ],
    });
    expect(parsed).toEqual([
      { id: "s1", name: "No instructions", instructions: "", enabled: true },
      { id: "s2", name: "Numeric instructions", instructions: "", enabled: true },
    ]);
  });

  it("falls back to an empty list for null/junk input or a missing/bad array", () => {
    expect(parseSkillList(null)).toEqual([]);
    expect(parseSkillList(undefined)).toEqual([]);
    expect(parseSkillList("nope")).toEqual([]);
    expect(parseSkillList(42)).toEqual([]);
    expect(parseSkillList({})).toEqual([]);
    expect(parseSkillList({ skills: "not-an-array" })).toEqual([]);
  });

  it("drops rows with no usable string id or name, and non-object rows", () => {
    const parsed = parseSkillList({
      skills: [
        { name: "No id", instructions: "x" }, // no id -> dropped
        { id: "", name: "Empty id", instructions: "x" }, // empty id -> dropped
        { id: "a", instructions: "x" }, // no name -> dropped
        { id: "b", name: "", instructions: "x" }, // empty name -> dropped
        { id: 7, name: "Numeric id" }, // id not a string -> dropped
        { id: "c", name: 9 }, // name not a string -> dropped
        "garbage", // not an object -> dropped
        null, // not an object -> dropped
        { id: "keep", name: "Kept", instructions: "yes", enabled: true }, // fully valid
      ],
    });
    expect(parsed).toEqual([
      { id: "keep", name: "Kept", instructions: "yes", enabled: true },
    ]);
  });
});
