// Appearance — the three-way theme choice and how it resolves.
//
// The fallback moved from "light" to "system" in the 2026-07-25 dark redesign
// (Appearance now defaults to "Match this computer"). The assertion below is
// updated to that intent rather than deleted: what still has teeth is that an
// EXPLICIT stored choice is never overridden by the OS preference — a person who
// picked light keeps light on a dark machine — and that only unreadable values
// fall through to following the computer.
import { describe, it, expect } from "vitest";
import { parseThemeChoice, resolveTheme } from "../lib/theme";

describe("resolveTheme", () => {
  it("returns the explicit choice regardless of the OS preference", () => {
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("light", false)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
    expect(resolveTheme("dark", true)).toBe("dark");
  });

  it("follows the OS preference when the choice is 'system'", () => {
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
  });
});

describe("parseThemeChoice", () => {
  it("preserves the three valid choices", () => {
    expect(parseThemeChoice("light")).toBe("light");
    expect(parseThemeChoice("dark")).toBe("dark");
    expect(parseThemeChoice("system")).toBe("system");
  });

  it("falls back to 'system' for absent or legacy/unknown values", () => {
    expect(parseThemeChoice(null)).toBe("system");
    expect(parseThemeChoice(undefined)).toBe("system");
    expect(parseThemeChoice("")).toBe("system");
    expect(parseThemeChoice("midnight")).toBe("system");
  });
});
