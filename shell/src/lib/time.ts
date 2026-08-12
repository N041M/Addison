// When something happened, in the one shape this app says it.
//
// Row times are a MACHINE FACT — `ui-monospace`, muted ink, never part of a row's
// accessible name. The format lives here rather than in a component because two
// lists now render it (the chat list in `Sidebar`, the Changes list on the Code
// screen), and two copies would drift into two idioms on two screens.

/** HH:MM today · the weekday within the last week · a short date beyond that ·
 * nothing at all when there is no usable timestamp.
 *
 * `at` is epoch SECONDS, the unit the core stores and sends. A zero/absent value
 * means we do not know when it happened, and the empty string is the honest answer
 * — dating it "today" would be inventing a fact. */
export function formatRowTime(at: number, now: Date = new Date()): string {
  if (!at) return "";
  const d = new Date(at * 1000);
  if (Number.isNaN(d.getTime())) return "";
  if (isSameDay(d, now)) {
    return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  }
  const days = (now.getTime() - d.getTime()) / 86_400_000;
  if (days >= 0 && days < 7) return d.toLocaleDateString(undefined, { weekday: "short" });
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** Same calendar day in the LOCAL zone — the only comparison "today" can mean to
 * somebody looking at their own clock. */
export function isSameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}
