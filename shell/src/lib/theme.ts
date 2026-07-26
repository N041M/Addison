// Appearance theme resolution (Settings → Appearance).
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
type ResolvedTheme = "light" | "dark";

// Absent, empty, and unrecognised values all map to "system" — "Match this
// computer" is the default as of the 2026-07-25 dark redesign, so a machine set
// to dark opens dark instead of forcing light on it.
//
// This CHANGED the fallback (it was "light"). The old comment argued that a
// person who had never opened Appearance couldn't be told apart from a fresh
// install, so light was the safe default. The redesign settles that the other
// way: a stored "light" is still honoured byte-for-byte, so the only launches
// this moves are the ones carrying no preference at all — where following the
// computer is the better guess, not a silent override of anyone's choice.
export function parseThemeChoice(raw: string | null | undefined): ThemeChoice {
  if (raw === "light" || raw === "dark" || raw === "system") return raw;
  return "system";
}

// The concrete light/dark the UI paints, given the choice and the OS preference.
export function resolveTheme(choice: ThemeChoice, systemPrefersDark: boolean): ResolvedTheme {
  if (choice === "system") return systemPrefersDark ? "dark" : "light";
  return choice;
}
