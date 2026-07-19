// Appearance theme resolution (Fern direction; Settings → Appearance).
//
// The user's choice is one of three: an explicit "light"/"dark", or "system"
// ("Match this computer"), which follows the OS's prefers-color-scheme live. The
// choice is what's persisted in localStorage ("addison.theme"); the concrete
// light/dark the UI actually paints is derived from it via resolveTheme.
//
// These are pure functions so the mapping is unit-tested (see theme.test.ts).
// The pre-paint bootstrap in index.html deliberately re-implements the same two
// rules inline in vanilla JS — it runs before this bundle loads, so it can't
// import from here; keep the two in lockstep.

export type ThemeChoice = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

// Legacy/absent localStorage values map to "light" — the historical default.
// Existing users either have no stored value or a literal "light"/"dark", so
// "light" as the fallback keeps their launch byte-for-byte; a fresh install
// lands on light too (we can't distinguish it from an existing user who never
// toggled, so defaulting to "system" would silently flip long-time users on a
// dark-set machine). "system" is only ever active when the user picks it.
export function parseThemeChoice(raw: string | null | undefined): ThemeChoice {
  if (raw === "light" || raw === "dark" || raw === "system") return raw;
  return "light";
}

// The concrete light/dark the UI paints, given the choice and the OS preference.
export function resolveTheme(choice: ThemeChoice, systemPrefersDark: boolean): ResolvedTheme {
  if (choice === "system") return systemPrefersDark ? "dark" : "light";
  return choice;
}
