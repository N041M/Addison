// The model lists' folder tree, shared by the composer menu and the Settings
// popup so the two cannot disagree about what a person is looking at.
//
// WHY THIS EXISTS. Both panels were designed for about five models: one row
// each, every row visible. Then registration started using the provider's real
// list instead of two hardcoded ids (2026-08-06), and a single connected Google
// key contributes twenty-two. A panel that shows all of them is not a menu, it
// is a scroll.
//
// WHAT IT IS — owner decision, 2026-08-07: "a two level folder — company - model
// family - model. Clicking on a level above should folder everything into said
// folder. Clicking on google should hide all others." So: a real accordion. One
// company open at a time, one family open inside it, and a closed folder shows
// its heading and nothing else.
//
// This SUPERSEDES the sidebar's "3 rows + 'N more…'" preview idiom FOR THESE TWO
// PANELS ONLY — design-brief-dark §4 still owns the sidebar's chat list, where a
// preview is still the right answer because those rows have no hierarchy to
// stand for them. Here they did: a preview is a peek into an open drawer, and
// what was asked for is a drawer that shuts.

/** Anything the panels render: it knows which company it sits under. */
export interface GroupedRow {
  group?: string;
  /** The model id, for deriving its family. Absent = no family level. */
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

/**
 * Which folders are open.
 *
 * THE ACCORDION IS THIS SHAPE, not a rule the callers have to remember. There is
 * one company field and one family field, so "opening Google closes Anthropic"
 * is not something either panel implements — it is the only thing the state can
 * say. Nothing anywhere can express two open companies.
 *
 * `family` belongs to `company`; it means nothing without one, and every
 * transition below rewrites the pair together.
 */
export interface FolderState {
  /** The one company drawn open, if any. */
  company?: string;
  /** The one family drawn open inside that company, if any. */
  family?: string;
}

/**
 * Where a row sits in the tree — the three numbers a panel announces.
 *
 * `posinset`/`setsize` ARE AUTHORED HERE BECAUSE NOTHING CAN COMPUTE THEM. Both
 * panels draw the tree as one flat run of `treeitem` siblings carrying
 * `aria-level`, with no `role="group"` around a folder's contents, and a screen
 * reader may only derive an ordinal from a DOM hierarchy that mirrors the tree.
 * Left unauthored it counts the whole list instead: with rows Anthropic(1),
 * opus(2), haiku(2), Google(1), the second of two companies announces as "level
 * 1, 4 of 4", and a family of four reads "5 of 10".
 *
 * They live in the row model rather than in either panel so that the two cannot
 * disagree about the ordinal for a row they both draw from the same call.
 */
interface RowPosition {
  /** 1 company, 2 family (or a model in a company with no family level), 3 model. */
  level: number;
  /** 1-based position among the children of the folder this row sits in. */
  posinset: number;
  /** How many children that folder has. */
  setsize: number;
}

export type ModelListRow<T> =
  /**
   * A company folder. `key` is both the label and what a caller toggles.
   * `total` is the models inside — the closed hint, in the brief's idiom.
   */
  | ({ kind: "company"; key: string; total: number; open: boolean } & RowPosition)
  /** A family folder inside the open company. `key` is the family name. */
  | ({ kind: "family"; key: string; company: string; total: number; open: boolean } & RowPosition)
  /** A model. `index` is its position in the ORIGINAL options array. */
  | ({ kind: "option"; option: T; index: number } & RowPosition);

/** Every row this module names itself: the folders, never a caller's model. */
export type ModelListFurnitureRow = Exclude<ModelListRow<never>, { kind: "option" }>;

/**
 * A folder row's identity for React, and the one place it is decided.
 *
 * Shared rather than spelled out in each panel, because the two ways of getting
 * this wrong are the same bug and it is not a visible one: duplicate keys made
 * React reconcile two rows against each other, and a fold left family labels
 * drawn over nothing (observed 2026-08-07, with a console full of "Encountered
 * two children with the same key"). Bucketing now makes duplicates impossible —
 * one row per company, one per family within it — so this is short. It stays
 * shared so that it cannot stop being true in one panel only.
 */
export function rowRenderKey(row: ModelListFurnitureRow): string {
  return row.kind === "company" ? `c:${row.key}` : `f:${row.company}/${row.key}`;
}

/** A model with the index it had in the caller's array, which it keeps. */
interface Placed<T> {
  option: T;
  index: number;
}

/**
 * Companies in order of FIRST APPEARANCE, each holding all of its models.
 *
 * Bucketed, where the flat list read runs and never re-ordered. The old
 * rationale was that the caller's order is authoritative and re-sorting would
 * move rows out from under a keyboard position — that argument INVERTS for a
 * folder tree: a company whose models arrive in two runs would draw as two
 * folders with the same name, and a family split across them as two folders that
 * open and close separately. That is not a folder, and no amount of ordering
 * fidelity is worth a menu that says "Google" twice.
 *
 * Order WITHIN a bucket is still the caller's, and `index` still points into the
 * caller's array, which is the part the keyboard actually depends on.
 */
function bucketByCompany<T extends GroupedRow>(
  options: T[],
): { company?: string; models: Placed<T>[] }[] {
  const order: (string | undefined)[] = [];
  const buckets = new Map<string | undefined, Placed<T>[]>();
  options.forEach((option, index) => {
    const company = option.group || undefined;
    let models = buckets.get(company);
    if (!models) {
      models = [];
      buckets.set(company, models);
      order.push(company);
    }
    models.push({ option, index });
  });
  return order.map((company) => ({ company, models: buckets.get(company) ?? [] }));
}

/**
 * The family folders inside one company, or `undefined` when that company has no
 * family level at all.
 *
 * FAMILIES ONLY EARN A LEVEL WHEN THEY GROUP SOMETHING. If every family in this
 * company holds exactly one model, each folder holds one model and says nothing
 * the model's own name does not — three folders for Opus/Sonnet/Haiku is a
 * drawer per sock. Google's twenty-two genuinely group; a three-model Anthropic
 * catalogue genuinely does not.
 *
 * When the level DOES apply, every family is a folder, singletons included: a
 * list mixing folder rows and loose model rows at the same indent asks a reader
 * to work out which is which on every line.
 */
function familyBuckets<T extends GroupedRow>(
  models: Placed<T>[],
): { family: string; models: Placed<T>[] }[] | undefined {
  const order: string[] = [];
  const buckets = new Map<string, Placed<T>[]>();
  for (const model of models) {
    // Nothing to name a folder after. One such model and the company keeps its
    // models directly, rather than growing a folder called "".
    if (!model.option.id) return undefined;
    const family = modelFamily(model.option.id);
    let inFamily = buckets.get(family);
    if (!inFamily) {
      inFamily = [];
      buckets.set(family, inFamily);
      order.push(family);
    }
    inFamily.push(model);
  }
  if (buckets.size >= models.length) return undefined;
  return order.map((family) => ({ family, models: buckets.get(family) ?? [] }));
}

/** The family folder an option sits in, or undefined where there is no level. */
function familyFolderOf<T extends GroupedRow>(options: T[], option: T): string | undefined {
  if (!option.id || !option.group) return undefined;
  const models = options
    .map((o, index) => ({ option: o, index }))
    .filter((m) => m.option.group === option.group);
  return familyBuckets(models) ? modelFamily(option.id) : undefined;
}

/**
 * The rows to draw, in order, for `options` under `state`.
 *
 * A CLOSED FOLDER IS ONE ROW. No preview, no "N more…" — that is the whole of
 * the 2026-08-07 change, and the reason a caller can reason about this list at
 * all: what is drawn is the path that is open, and nothing else.
 *
 * `index` on a model row is its position in the ORIGINAL array, so a caller
 * holding indices keeps meaning them however the folders move. The popup's
 * anchor row — the one it positions itself by — is the consumer.
 */
export function modelListRows<T extends GroupedRow>(
  options: T[],
  state: FolderState,
): ModelListRow<T>[] {
  const rows: ModelListRow<T>[] = [];
  const buckets = bucketByCompany(options);
  // The root level holds a row per company PLUS every option a caller left
  // ungrouped: `aria-setsize` is what is drawn at that level, not what a tidier
  // catalogue would have drawn.
  const roots = buckets.reduce((n, b) => n + (b.company ? 1 : b.models.length), 0);
  let rootAt = 0;
  for (const bucket of buckets) {
    if (!bucket.company) {
      // A caller that grouped nothing gets exactly what it passed, undecorated —
      // there is no folder to open, so there is nothing to close either.
      for (const m of bucket.models) {
        rootAt += 1;
        rows.push({
          kind: "option",
          option: m.option,
          index: m.index,
          level: 1,
          posinset: rootAt,
          setsize: roots,
        });
      }
      continue;
    }

    const company = bucket.company;
    const open = state.company === company;
    rootAt += 1;
    rows.push({
      kind: "company",
      key: company,
      level: 1,
      total: bucket.models.length,
      open,
      posinset: rootAt,
      setsize: roots,
    });
    if (!open) continue;

    const families = familyBuckets(bucket.models);
    if (!families) {
      bucket.models.forEach((m, at) => {
        rows.push({
          kind: "option",
          option: m.option,
          index: m.index,
          level: 2,
          posinset: at + 1,
          setsize: bucket.models.length,
        });
      });
      continue;
    }
    families.forEach((family, familyAt) => {
      const familyOpen = state.family === family.family;
      rows.push({
        kind: "family",
        key: family.family,
        company,
        level: 2,
        total: family.models.length,
        open: familyOpen,
        posinset: familyAt + 1,
        setsize: families.length,
      });
      if (!familyOpen) return;
      family.models.forEach((m, at) => {
        rows.push({
          kind: "option",
          option: m.option,
          index: m.index,
          level: 3,
          posinset: at + 1,
          setsize: family.models.length,
        });
      });
    });
  }
  return rows;
}

/**
 * The option the folders open around: the one in effect, or the first when
 * nothing is.
 *
 * ONE RULE, shared by the seed and the toggle below. They used to disagree —
 * the seed fell back to option 0 and opened its family, the toggle fell back to
 * nothing and left every family shut — so with no selection the same company
 * click produced two different trees depending on whether it was the opening or
 * a press. The fallback is the popup's own: its positioning aims at the first
 * row when there is nothing selected to aim at.
 */
function anchorOption<T extends GroupedRow>(
  options: T[],
  isSelected: (option: T) => boolean,
): T | undefined {
  return options[Math.max(0, options.findIndex(isSelected))];
}

/**
 * The folders open when a menu opens: the path to the model in effect.
 *
 * THE POINT OF THE FUNCTION, and the reason it is not a constant. A menu opening
 * on a closed tree says nothing about what is on, which is the one thing it
 * exists to say — so the company holding the selection opens, and its family
 * with it. Everything else stays shut, which is what the person asked for.
 */
export function initialFolderState<T extends GroupedRow>(
  options: T[],
  isSelected: (option: T) => boolean,
): FolderState {
  const option = anchorOption(options, isSelected);
  if (!option?.group) return {};
  return { company: option.group, family: familyFolderOf(options, option) };
}

/**
 * Clicking a company folder.
 *
 * Opening one closes every other company AND any family that was open inside the
 * old one — that is the ask, stated as a state transition. A company opened by
 * HAND starts with its families all closed, unless it happens to hold the model
 * in effect, in which case that family opens too: opening the company you are
 * already using should show you where you are, and opening any other should not
 * pre-empt what you came to look for. With nothing in effect, `anchorOption`'s
 * one rule decides — the same tree the menu would have opened on.
 */
export function toggleCompany<T extends GroupedRow>(
  state: FolderState,
  company: string,
  options: T[],
  isSelected: (option: T) => boolean,
): FolderState {
  if (state.company === company) return {};
  const selected = anchorOption(options, isSelected);
  const family =
    selected && selected.group === company ? familyFolderOf(options, selected) : undefined;
  return { company, family };
}

/** Clicking a family folder: the same accordion, one level down. */
export function toggleFamily(state: FolderState, family: string): FolderState {
  return { company: state.company, family: state.family === family ? undefined : family };
}
