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

  it("falls back to 'light' for absent or legacy/unknown values", () => {
    expect(parseThemeChoice(null)).toBe("light");
    expect(parseThemeChoice(undefined)).toBe("light");
    expect(parseThemeChoice("")).toBe("light");
    expect(parseThemeChoice("midnight")).toBe("light");
  });
});
