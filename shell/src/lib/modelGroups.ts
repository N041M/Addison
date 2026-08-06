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
  /** The model id, for deriving its family. Absent = no family row. */
  id?: string;
}

// Words that are a PRODUCT rather than a version, so the family is the next
// segment along: "claude-opus-4-8" is Opus, not Claude, and "gemini-2.5-pro" is
// 2.5, not Gemini. Everything else takes its first segment, which is right for
// "gpt-4.1", "o4-mini" and a local "llama3" alike.
const _PRODUCT_PREFIXES = new Set(["claude", "gemini", "gemma", "gpt", "llama", "qwen", "mistral"]);
// Rendered upper-case: these read as shouting in title case and as typos in lower.
const _ACRONYMS = new Set(["gpt", "tts"]);

function _titled(word: string): string {
  if (_ACRONYMS.has(word)) return word.toUpperCase();
  return word.charAt(0).toUpperCase() + word.slice(1);
}

/**
 * The family a model id belongs to — "Claude Opus", "Gemini 2.5", "Gemma 4".
 *
 * STRUCTURAL, like the alias dedup and unlike the non-chat denylist: it reads the
 * id's own shape rather than a table of known models, so a family nobody has
 * heard of still groups correctly and nothing needs maintaining when a provider
 * ships a new generation.
 *
 * Two segments, because one is never enough and three are always too many.
 * "Claude" alone puts Opus and Haiku together; "Claude Opus 4.8" is not a family
 * at all, it is the model. The middle is the useful grain, and it is the grain a
 * person names out loud — "put it on Opus", "is 2.5 still around?".
 *
 * The axis differs per vendor and that is CORRECT, not a compromise: Anthropic
 * names tiers (opus/sonnet/haiku) and Google names generations (2.5/3.1), so the
 * second segment lands on whichever one that vendor actually uses to distinguish
 * its models.
 */
export function modelFamily(id: string): string {
  const parts = id.split("-").filter(Boolean);
  if (parts.length === 0) return id;
  const head = parts[0].toLowerCase();
  if (_PRODUCT_PREFIXES.has(head) && parts.length > 1) {
    return `${_titled(head)} ${_titled(parts[1])}`;
  }
  return _titled(parts[0]);
}

/** How many rows a collapsed group still shows. The brief's number, not a guess. */
export const COLLAPSED_ROW_COUNT = 3;

export type ModelListRow<T> =
  /** The company, and the fold control: `key` is what a caller toggles. */
  | { kind: "heading"; key: string; total: number; collapsed: boolean }
  /** A family inside an EXPANDED company - a quiet label, never foldable. */
  | { kind: "family"; key: string; family: string }
  | { kind: "option"; option: T; index: number }
  | { kind: "more"; key: string; hidden: number };

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
    // A RUN, not a bucket. The core already emits a provider's models together,
    // so reading runs preserves the order the caller chose; bucketing would
    // re-sort the menu out from under a keyboard position it is tracking.
    let end = i;
    while (end < options.length && options[end].group === group) end += 1;
    const run = options.slice(i, end);

    if (!group) {
      for (let k = 0; k < run.length; k += 1) {
        rows.push({ kind: "option", option: run[k], index: i + k });
      }
      i = end;
      continue;
    }

    const isCollapsed = collapsed.has(group);
    rows.push({ kind: "heading", key: group, total: run.length, collapsed: isCollapsed });

    if (isCollapsed) {
      // FLAT while folded, and that is the point of folding: a three-row preview
      // interrupted by family labels would spend its three rows on furniture.
      const shown = Math.min(COLLAPSED_ROW_COUNT, run.length);
      for (let k = 0; k < shown; k += 1) rows.push({ kind: "option", option: run[k], index: i + k });
      if (shown < run.length) rows.push({ kind: "more", key: group, hidden: run.length - shown });
    } else {
      // EXPANDED, so the families earn their keep: nineteen Gemini ids in one
      // column is a wall, and the same nineteen under "Gemini 3.1", "Gemini 2.5",
      // "Gemma 4" is a list somebody can scan. The families do NOT fold — real
      // ones hold one to three models, so a fold there would almost never fire
      // and would cost a heading per model to say so.
      let lastFamily: string | undefined;
      for (let k = 0; k < run.length; k += 1) {
        const option = run[k];
        const family = option.id ? modelFamily(option.id) : undefined;
        if (family && family !== lastFamily) {
          rows.push({ kind: "family", key: `${group} ${family}`, family });
          lastFamily = family;
        }
        rows.push({ kind: "option", option, index: i + k });
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
    const key = option.group;
    const seen = counts.get(key) ?? 0;
    counts.set(key, seen + 1);
    if (isSelected(option) && !selectedRank.has(key)) selectedRank.set(key, seen);
  }
  const collapsed = new Set<string>();
  for (const [key, total] of counts) {
    if (total <= COLLAPSED_ROW_COUNT) continue;
    const rank = selectedRank.get(key);
    // Already among the visible three? Then folding still shows it, and opening
    // the whole family costs rows without telling anyone anything.
    if (rank !== undefined && rank >= COLLAPSED_ROW_COUNT) continue;
    collapsed.add(key);
  }
  return collapsed;
}
