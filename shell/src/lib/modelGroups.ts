// Grouping + collapse for the model lists, shared by the composer popup and the
// Settings selector so the two cannot disagree about what a person is looking at.
//
// WHY THIS EXISTS NOW. Both panels were designed for about five models: 270px
// wide, one row each, every row visible. Then registration started using the
// provider's real list instead of two hardcoded ids (2026-08-06), and a single
// connected Google key contributes twenty-two. A panel that shows all of them is
// not a menu, it is a scroll.
//
// The shape is taken from the brief rather than invented: the sidebar's chat list
// already solves exactly this — "Group header row: label + mono hint (count, or
// 'collapse'); click toggles. Collapsed groups show 3 rows + 'N more…' row"
// (design-brief-dark/README.md §7). Reusing it means no new vocabulary, and a
// person who has learned one list has learned both.

/** Anything the panels render: it knows which company heading it sits under. */
export interface GroupedRow {
  group?: string;
}

/** How many rows a collapsed group still shows. The brief's number, not a guess. */
export const COLLAPSED_ROW_COUNT = 3;

export type ModelListRow<T> =
  | { kind: "heading"; group: string; total: number; collapsed: boolean }
  | { kind: "option"; option: T; index: number }
  | { kind: "more"; group: string; hidden: number };

/**
 * The rows to draw, in order, for `options` under `collapsed`.
 *
 * ORDER IS THE CALLER'S, never this function's. Headings are emitted where the
 * group CHANGES rather than by bucketing, for the same reason the ungrouped
 * version did it that way: the core already emits a provider's models together,
 * and a panel that re-sorted would move the selection out from under a keyboard
 * position its caller is tracking.
 *
 * `index` on an option row is its position in the ORIGINAL array, so a caller
 * holding indices (the selector's `activeIndex`, its `optionId`) keeps meaning
 * them even as rows appear and disappear underneath.
 */
export function modelListRows<T extends GroupedRow>(
  options: T[],
  collapsed: ReadonlySet<string>,
): ModelListRow<T>[] {
  const rows: ModelListRow<T>[] = [];
  let i = 0;
  while (i < options.length) {
    const group = options[i].group;
    // A run, not a bucket: the same company appearing twice would legitimately
    // produce two headings, and that is the caller's business to avoid.
    let end = i;
    while (end < options.length && options[end].group === group) end += 1;
    const run = options.slice(i, end);

    if (group) {
      const isCollapsed = collapsed.has(group);
      rows.push({ kind: "heading", group, total: run.length, collapsed: isCollapsed });
      const shown = isCollapsed ? Math.min(COLLAPSED_ROW_COUNT, run.length) : run.length;
      for (let k = 0; k < shown; k += 1) rows.push({ kind: "option", option: run[k], index: i + k });
      if (shown < run.length) {
        rows.push({ kind: "more", group, hidden: run.length - shown });
      }
    } else {
      for (let k = 0; k < run.length; k += 1) {
        rows.push({ kind: "option", option: run[k], index: i + k });
      }
    }
    i = end;
  }
  return rows;
}

/**
 * Which groups start collapsed: the long ones, minus whichever holds the row the
 * person is currently on.
 *
 * That exception is the whole reason this is a function rather than a constant.
 * Collapsing by size alone hides the ACTIVE model whenever it sits fourth or
 * lower in its company — so the menu would open saying nothing about what is in
 * effect, which is the one thing it exists to say.
 */
export function initialCollapsedGroups<T extends GroupedRow>(
  options: T[],
  isSelected: (option: T) => boolean,
): Set<string> {
  const counts = new Map<string, number>();
  const selectedRank = new Map<string, number>();
  for (const option of options) {
    if (!option.group) continue;
    const seen = counts.get(option.group) ?? 0;
    counts.set(option.group, seen + 1);
    if (isSelected(option) && !selectedRank.has(option.group)) {
      selectedRank.set(option.group, seen);
    }
  }
  const collapsed = new Set<string>();
  for (const [group, total] of counts) {
    if (total <= COLLAPSED_ROW_COUNT) continue;
    const rank = selectedRank.get(group);
    // Already visible in the first COLLAPSED_ROW_COUNT rows? Then collapsing
    // this group still shows it, and there is no reason to open the whole thing.
    if (rank !== undefined && rank >= COLLAPSED_ROW_COUNT) continue;
    collapsed.add(group);
  }
  return collapsed;
}
