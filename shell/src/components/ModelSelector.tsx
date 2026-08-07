// The composer's model label + its menu (DARK direction; docs/design-brief-dark,
// "Screens → Composer model menu"). Replaces the Fern popover.
//
// The label is a mono 10.5px machine fact at the right of the composer row;
// clicking it opens a small panel ABOVE it, anchored bottom-right: an "Answer
// with" header over rows of `name … note`, where the note reads `local` for a
// model on this computer and `quality` for a cloud one, and the selected row
// carries a ✓ in accent. When the chosen cloud model offers levels of effort, an
// "Effort" section follows in the same row idiom. A footer hint says where the
// permanent choice lives: this menu is per message.
//
// THE MODEL LIST IS A FOLDER TREE (owner decision 2026-08-07 — company → family
// → model, one folder open at a time). `lib/modelGroups.ts` owns the shape and
// the reasoning; this file draws it and gives it a keyboard.
//
// ROWS ARE THE REAL CATALOG. Every entry comes from `model.availableRoles` —
// cloud models from the connected providers, plus whatever is set up under the
// local role. `free` is never assumed: the note says so only if the core's own
// flag says so, and no cloud model carries it today (agent_core's CloudModel
// keeps `free` off the wire, and the free chip stays Ollama-only — CLAUDE.md,
// Phase-2 step 4). Inventing it here would be the app claiming a model costs
// nothing on its own authority.
//
// ACCESSIBILITY IS NOT RESTYLED AWAY. The readers are 54 and 68: the label stays
// a real button (aria-haspopup="tree"), the list is a role="tree" of
// role="treeitem" rows carrying aria-level, aria-expanded and — because the rows
// are flat siblings rather than nested groups — the aria-posinset/aria-setsize
// that `lib/modelGroups.ts` authors for them. The keys are the WAI-ARIA tree's,
// with ONE deliberate departure: Up and Down WRAP at the ends, because this is a
// menu of a dozen rows and walking off the bottom to reach the top is what a
// menu does (pinned by modelSelectorFolding.test.tsx). Right opens a closed
// folder and steps into an open one; on a model it does nothing, which is where
// the pattern says a leaf ends. Left closes an open folder or climbs to its
// parent, Enter picks, focus moves into the tree on open and back to the label
// on close, and outside-click dismisses. A listbox cannot say "folder", which is
// why this is no longer one.
//
// TAB WALKS THE PANEL, it does not leave it. Tab used to close the menu
// outright, which meant the three `aria-pressed` Effort buttons could never take
// focus: a keyboard user could choose a model but never the effort the composer
// label is advertising back at them ("Claude Opus 4.8 · high"). Tab now cycles
// tree → effort → tree; Escape is the way out and returns focus to the label.
// When there is no Effort section there is nothing to cycle to, so Tab keeps its
// old behaviour and leaves.

import {
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import { isMotionEnabled } from "../lib/scramble";
import type { ModelRole } from "../types/protocol";
import type { CloudModel, RoleOption } from "../types/ui";
import {
  initialFolderState,
  modelListRows,
  rowRenderKey,
  toggleCompany,
  toggleFamily,
  type FolderState,
  type ModelListRow,
} from "../lib/modelGroups";

interface Props {
  roles: RoleOption[];
  cloudModels: CloudModel[];
  selectedRole: ModelRole;
  selectedCloudModel?: string;
  selectedLocalModel?: string;
  selectedEffort?: string;
  /** Choose a model — carries its role (cloud = "primary", local) with it. */
  onSelectModel: (role: ModelRole, modelId: string) => void;
  onSelectEffort: (effort: string) => void;
  disabled?: boolean;
}

// Shown only when there's no real catalog yet — e.g. opened in a plain browser
// for design review, before the engine is connected. It keeps the control
// visible and laid out (and inert), rather than vanishing. The engine always
// replaces this with the core's real catalog once connected.
const PLACEHOLDER_CLOUD: CloudModel[] = [
  {
    id: "claude-opus-4-8",
    label: "Claude Opus 4.8",
    effortLevels: [
      { id: "low", label: "low" },
      { id: "high", label: "high" },
      { id: "xhigh", label: "xhigh" },
    ],
    default: true,
  },
  {
    id: "claude-sonnet-5",
    label: "Claude Sonnet 5",
    effortLevels: [
      { id: "low", label: "low" },
      { id: "high", label: "high" },
      { id: "xhigh", label: "xhigh" },
    ],
    default: false,
  },
  {
    id: "claude-haiku-4-5",
    label: "Claude Haiku 4.5",
    effortLevels: [],
    default: false,
  },
];

function defaultCloud(models: CloudModel[]): CloudModel | undefined {
  return models.find((m) => m.default) ?? models[0];
}

/** One selectable row in the menu, flattened across cloud + local. */
interface Option {
  role: ModelRole;
  id: string;
  /** Row label — the model's own name, never provider-suffixed (see `group`). */
  label: string;
  /** The company folder this row sits under ("Google", "On this computer"). */
  group: string;
  /** The mono note at the right of the row: "local" | "free" | "quality". */
  note: string;
  /**
   * The provider's own words for refusing this model, when the core has watched
   * it happen. The SENTENCE, not a flag: the strike-through says a row was
   * refused and only this says why.
   */
  unavailable?: string;
  current: boolean;
}

/** How long the menu takes to fade back down. Shorter than the .2s it rises on:
 * an exit that matches its entrance reads as sluggish, because nobody is waiting
 * to read anything on the way out. */
const MENU_EXIT_MS = 140;

/** Left padding per tree level, indexed by `level - 1` — levels are 1-based. */
const INDENT = ["pl-2.5", "pl-5", "pl-7"];

/** The empty catalogue, as one value. A fresh `[]` per render would churn the
 * memo below for a person with no local model set up — which is most of them. */
const NO_MODELS: { id: string; label: string }[] = [];

export function ModelSelector({
  roles,
  cloudModels,
  selectedRole,
  selectedCloudModel,
  selectedLocalModel,
  selectedEffort,
  onSelectModel,
  onSelectEffort,
  disabled,
}: Props) {
  const localModels = roles.find((r) => r.role === "local" && r.configured)?.models ?? NO_MODELS;

  // Nothing real to offer yet (disconnected design review): fall back to inert
  // placeholders so the row still reads as part of the composer.
  const usingPlaceholder = cloudModels.length === 0 && localModels.length === 0;
  const cloud = usingPlaceholder ? PLACEHOLDER_CLOUD : cloudModels;
  const locals = usingPlaceholder ? NO_MODELS : localModels;
  // Two distinct notions: `blockOpen` (a turn is running — don't let the picker
  // open at all) vs `dimmed`/inert placeholder mode (disconnected design review —
  // the label is dimmed but the menu still opens so the catalog is *browsable*;
  // picking is a no-op until a real engine is connected).
  const blockOpen = Boolean(disabled);
  const dimmed = Boolean(disabled) || usingPlaceholder;

  // The model that's currently in effect.
  const activeCloud = cloud.find((m) => m.id === selectedCloudModel) ?? defaultCloud(cloud);
  const activeLocalId = selectedLocalModel ?? locals[0]?.id;

  const onLocal = selectedRole === "local" && locals.length > 0;

  // Effort only applies to the chosen cloud model; local models never carry it.
  const effortLevels = onLocal ? [] : (activeCloud?.effortLevels ?? []);

  // Which level reads as active. App keeps `selectedEffort` reconciled to the
  // model, but fall back to the middle/default level so a sensible one is always
  // lit — including inert placeholder mode, where App never reconciles it.
  const middleEffort =
    effortLevels.length > 0 ? effortLevels[Math.floor(effortLevels.length / 2)].id : undefined;
  const activeEffort = effortLevels.some((l) => l.id === selectedEffort)
    ? selectedEffort
    : middleEffort;

  // Attribution moved from a per-row suffix ("GPT-4.1 — OpenAI") to a company
  // folder, so it is said once per company instead of once per row — and the row
  // keeps its full width for the model's own name.
  //
  // Memoised because this component re-renders with the composer, which is on
  // every keystroke of a message: the rows below are built from these, and
  // rebuilding a folder tree nobody has opened is work done for a menu that is
  // not on screen.
  const options: Option[] = useMemo(
    () => [
      ...cloud.map((m) => ({
        role: "primary" as ModelRole,
        id: m.id,
        label: m.label,
        group: m.providerLabel ?? "Cloud",
        // Only the core may call a model free, and only the core may call one
        // refused: both are reports off the wire (see the file header).
        note: m.unavailable ? "unavailable" : m.free ? "free" : "quality",
        unavailable: m.unavailable,
        current: !onLocal && m.id === activeCloud?.id,
      })),
      ...locals.map((m) => ({
        role: "local" as ModelRole,
        id: m.id,
        label: m.label,
        group: "On this computer",
        note: "local",
        current: onLocal && m.id === activeLocalId,
      })),
    ],
    [cloud, locals, onLocal, activeCloud?.id, activeLocalId],
  );
  const isCurrent = (o: Option) => o.current;
  const currentOption = options[Math.max(0, options.findIndex(isCurrent))];

  // The composer's label: the active model, plus its effort word when it has one.
  const activeLabel = onLocal
    ? (locals.find((m) => m.id === activeLocalId)?.label ?? activeLocalId ?? "Model")
    : (activeCloud?.label ?? "Model");
  const activeEffortLabel = effortLevels.find((l) => l.id === activeEffort)?.label;

  // ---- Menu open/close + keyboard state -----------------------------------
  const [open, setOpen] = useState(false);
  // The menu stays mounted for the length of its exit. Without this it faded and
  // rose IN and then vanished on a single frame when it closed — "it just plops
  // out" (reported 2026-07-26). Every close path goes through `setOpen(false)`
  // (Escape, an outside click, picking a model or an effort, the composer being
  // blocked mid-turn), so driving the exit off `open` covers all of them.
  const [exiting, setExiting] = useState(false);
  const wasOpen = useRef(false);
  useEffect(() => {
    if (open) {
      wasOpen.current = true;
      setExiting(false);
      return;
    }
    if (!wasOpen.current) return; // never opened, nothing to animate away
    wasOpen.current = false;
    if (!isMotionEnabled()) return;
    setExiting(true);
    const timer = setTimeout(() => setExiting(false), MENU_EXIT_MS);
    return () => clearTimeout(timer);
  }, [open]);
  // DERIVED, not state: the panel has to exist on the very render that opens it,
  // or the effect below has nothing to move focus to and the menu opens with
  // focus stranded on <body>. Asking for it a render later cost exactly that.
  const menuPresent = open || exiting;

  // Which folders are open. Seeded when the menu OPENS (below) and never held
  // across openings: the menu is a glance surface, and yesterday's expansion is
  // a stale answer to "what is on right now?". Closed at mount, so there is
  // nothing to seed here — a tree nobody is looking at has no open path.
  const [folders, setFolders] = useState<FolderState>({});
  // THERE IS ALWAYS AT LEAST ONE ROW: `usingPlaceholder` guarantees a non-empty
  // catalogue, and `modelListRows` draws at least a folder for it. The cursor
  // below therefore always has somewhere to be, and no branch here has to
  // pretend otherwise.
  const rows = useMemo(() => modelListRows(options, folders), [options, folders]);
  /** What a row IS, across a fold: a model keeps its identity when its folders
   * move, so the keyboard's cursor survives an accordion it did not ask for. */
  const rowKeyOf = (row: ModelListRow<Option>) =>
    row.kind === "option" ? `m:${row.option.role}:${row.option.id}` : rowRenderKey(row);
  const rowKeys = rows.map(rowKeyOf);
  const [activeKey, setActiveKey] = useState<string>("");
  // RESOLVED AGAINST WHAT IS DRAWN, every render. `aria-activedescendant` naming
  // an element that is not in the document is the classic failure of this
  // pattern — a screen reader is told the cursor is on nothing — and an
  // accordion closes over the cursor from three directions (its own folder, a
  // parent, a sibling company opening). Deriving the id from the rendered rows
  // makes that unrepresentable rather than a case to remember.
  const activeAt = Math.max(0, rowKeys.indexOf(activeKey));
  const treeId = useId();
  const rowDomId = (i: number) => `${treeId}-row-${i}`;

  /** Move the cursor, wrapping at both ends — see the file header for why a
   * menu wraps where a tree would stop. */
  const stepActive = (delta: number) => {
    setActiveKey(rowKeys[(activeAt + delta + rows.length) % rows.length]);
  };
  /** The folder row this row sits inside: the first shallower row above it. */
  const parentAt = (at: number) => {
    const level = rows[at].level;
    for (let i = at - 1; i >= 0; i -= 1) if (rows[i].level < level) return i;
    return at;
  };
  function activateFolder(row: ModelListRow<Option>) {
    if (row.kind === "option") return;
    setFolders((prev) =>
      row.kind === "company"
        ? toggleCompany(prev, row.key, options, isCurrent)
        : toggleFamily(prev, row.key),
    );
    // The cursor follows the folder that was acted on. It is the one row certain
    // to survive the accordion — opening Google closes Anthropic underneath a
    // cursor that was standing in it.
    setActiveKey(rowRenderKey(row));
  }

  const rootRef = useRef<HTMLDivElement | null>(null);
  const labelRef = useRef<HTMLButtonElement | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const effortRef = useRef<HTMLDivElement | null>(null);

  // On open, open the path to the model in effect, put the cursor on it, and
  // move focus into the tree (aria-activedescendant pattern).
  useLayoutEffect(() => {
    if (open) {
      setFolders(initialFolderState(options, isCurrent));
      setActiveKey(currentOption ? `m:${currentOption.role}:${currentOption.id}` : "");
      listRef.current?.focus();
    }
    // Only re-run when the menu toggles.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Keep the cursor's row scrolled into view as it walks, and after a fold moves
  // the rows under it.
  useEffect(() => {
    if (!open) return;
    document.getElementById(rowDomId(activeAt))?.scrollIntoView({ block: "nearest" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeKey, folders, open]);

  // Outside-click closes.
  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  // A turn starting while the menu is open would leave a menu over a composer
  // that can no longer act on it.
  useEffect(() => {
    if (blockOpen) setOpen(false);
  }, [blockOpen]);

  function close(returnFocus = true) {
    setOpen(false);
    if (returnFocus) labelRef.current?.focus();
  }

  function pickModel(o: Option) {
    // Inert while showing placeholders (disconnected): browse, but don't commit.
    if (!usingPlaceholder) onSelectModel(o.role, o.id);
    close();
  }

  function onTreeKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    const row = rows[activeAt];
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        stepActive(1);
        break;
      case "ArrowUp":
        e.preventDefault();
        stepActive(-1);
        break;
      case "ArrowRight":
        // A closed folder opens; an open one steps to the first row inside it,
        // which is the next row drawn; a model has nothing to open, so Right
        // stops there. This is how a keyboard reaches a folder at all: the rows
        // are clickable for the mouse, but putting each one in the tab order
        // would break the panel's Tab contract (tree -> effort -> tree), so the
        // tree grows the left/right idiom instead of the menu growing tab stops.
        e.preventDefault();
        if (row.kind === "option") break;
        if (row.open) stepActive(1);
        else activateFolder(row);
        break;
      case "ArrowLeft": {
        e.preventDefault();
        // An open folder closes; anything else climbs to the folder it is in.
        if (row.kind !== "option" && row.open) {
          activateFolder(row);
          break;
        }
        const parent = parentAt(activeAt);
        if (parent !== activeAt) setActiveKey(rowKeys[parent]);
        break;
      }
      case "Home":
        e.preventDefault();
        setActiveKey(rowKeys[0]);
        break;
      case "End":
        e.preventDefault();
        setActiveKey(rowKeys[rows.length - 1]);
        break;
      case "Enter":
      case " ":
        e.preventDefault();
        if (row.kind === "option") pickModel(row.option);
        else activateFolder(row);
        break;
      default:
        break;
    }
    // Escape and Tab are handled once, on the panel, so they behave the same
    // whether focus is on the tree or on an Effort button.
  }

  /** The panel's tab ring, in DOM order: the model tree, then each Effort
   * button. */
  function panelStops(): HTMLElement[] {
    const stops: HTMLElement[] = [];
    if (listRef.current) stops.push(listRef.current);
    if (effortRef.current) {
      stops.push(...Array.from(effortRef.current.querySelectorAll<HTMLElement>("button")));
    }
    return stops;
  }

  function onPanelKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
      return;
    }
    if (e.key !== "Tab") return;
    const stops = panelStops();
    if (stops.length < 2) {
      // Nothing else in here to reach (a model with no effort levels, or a local
      // one): let focus leave naturally and take the menu with it, as before.
      close(false);
      return;
    }
    e.preventDefault();
    const i = stops.indexOf(document.activeElement as HTMLElement);
    const next = e.shiftKey
      ? stops[(i <= 0 ? stops.length : i) - 1]
      : stops[(i + 1) % stops.length];
    next.focus();
  }

  return (
    <span ref={rootRef} className="relative inline-flex min-w-0">
      <button
        ref={labelRef}
        type="button"
        data-scramble="440"
        aria-haspopup="tree"
        aria-expanded={open}
        disabled={blockOpen}
        aria-label={
          activeEffortLabel
            ? `Model: ${activeLabel}, effort ${activeEffortLabel}. Choose model and effort.`
            : `Model: ${activeLabel}. Choose model.`
        }
        onClick={() => !blockOpen && setOpen((v) => !v)}
        className={
          // Narrower on a phone: the label shares one row with the textarea, and
          // a full model name there squeezes the message down to a keyhole.
          "max-w-[110px] truncate font-mono text-[10.5px] transition-colors max-md:min-h-[44px] md:max-w-[180px] " +
          (open ? "text-muted" : "text-disabled hover:text-muted ") +
          // Dark only. On light, `disabled` at 60% measured 1.55:1 against the
          // paper — the label that names the model in effect was the least
          // readable text in the app. The light ramp carries the dimming now.
          (dimmed ? " dark:opacity-60" : "") +
          (blockOpen ? " cursor-not-allowed" : "")
        }
      >
        {activeLabel}
        {activeEffortLabel ? ` · ${activeEffortLabel.toLowerCase()}` : ""}
      </button>

      {menuPresent && (
        <div
          onKeyDown={onPanelKeyDown}
          // `w-max` over the 196px floor: a model picker that abbreviates the
          // model name ("Claude Opus 4…") is asking people to choose between
          // things it won't show them. The panel grows to its content and only
          // truncates past 320px, where a provider-suffixed name would otherwise
          // push the menu off the column.
          //
          // The height clamp is the viewport one: the menu opens upward from the
          // composer, so in a short window it ran past the top of the app and
          // over the header (measured at 1280×420: menu top 76, header bottom
          // 77). It scrolls instead of climbing.
          className={
            "no-scrollbar absolute bottom-[26px] right-0 z-30 flex max-h-[calc(100vh-160px)] w-max min-w-[196px] max-w-[320px] flex-col overflow-y-auto rounded-[6px] border border-rail bg-panel p-1.5 shadow-menu " +
            (open
              ? "animate-[fadeRise_.2s_ease_both]"
              : "pointer-events-none animate-[fadeDrop_.14s_ease_both]")
          }
          role="presentation"
          // While it fades out the menu is a picture, not a control: out of the
          // a11y tree, and not clickable. Screen readers and tests both see it
          // gone the moment it closes, which is what "closed" should mean.
          aria-hidden={!open}
        >
          <div className="px-2.5 pb-2 pt-1.5 text-[10px] font-medium tracking-[.04em] text-faint">
            Answer with
          </div>

          <div
            ref={listRef}
            role="tree"
            tabIndex={-1}
            aria-label="Which model Addison uses"
            aria-activedescendant={rowDomId(activeAt)}
            onKeyDown={onTreeKeyDown}
            className="no-scrollbar max-h-[40vh] overflow-y-auto outline-none"
          >
            {rows.map((row, i) => {
              const active = i === activeAt;
              if (row.kind !== "option") {
                return (
                  <button
                    key={rowRenderKey(row)}
                    id={rowDomId(i)}
                    type="button"
                    role="treeitem"
                    aria-level={row.level}
                    // Authored, not inferred: these rows are flat siblings, so
                    // nothing can count a folder's children but us
                    // (lib/modelGroups.ts).
                    aria-posinset={row.posinset}
                    aria-setsize={row.setsize}
                    aria-expanded={row.open}
                    // OUT of the tab order (tabIndex -1): Tab cycles tree ->
                    // effort -> tree in this panel, and adding a stop per folder
                    // would rewrite that contract for anyone who already knows
                    // it. The keyboard folds with Left/Right on the row instead.
                    tabIndex={-1}
                    onClick={() => activateFolder(row)}
                    onMouseEnter={() => setActiveKey(rowRenderKey(row))}
                    className={
                      "flex w-full items-baseline gap-2 py-[5px] pr-2.5 text-left font-mono " +
                      "text-[10px] transition-colors hover:text-muted " +
                      INDENT[row.level - 1] +
                      (active ? " bg-line text-muted" : " text-disabled")
                    }
                  >
                    {/* Upper-case belongs to the family LABEL, not the row — on
                        the row it shouted the "collapse" hint too. */}
                    <span
                      className={
                        "min-w-0 truncate" +
                        (row.kind === "family" ? " uppercase tracking-wider" : "")
                      }
                    >
                      {row.key}
                    </span>
                    <span className="flex-1" />
                    <span className="shrink-0">{row.open ? "collapse" : row.total}</span>
                  </button>
                );
              }
              const o = row.option;
              return (
                <div
                  key={`${o.role}:${o.id}`}
                  id={rowDomId(i)}
                  role="treeitem"
                  aria-level={row.level}
                  aria-posinset={row.posinset}
                  aria-setsize={row.setsize}
                  aria-selected={o.current}
                  onClick={() => pickModel(o)}
                  onMouseEnter={() => setActiveKey(`m:${o.role}:${o.id}`)}
                  className={
                    "flex cursor-pointer flex-col rounded-[4px] py-[7px] pr-2.5 " +
                    INDENT[row.level - 1] +
                    (active ? " bg-line" : "")
                  }
                >
                  <span className="flex w-full items-baseline gap-2.5">
                    <span
                      className={
                        "min-w-0 truncate font-mono text-[10.5px] " +
                        // Refused beats current: a model the core has watched a
                        // provider refuse reads as struck through even where it
                        // is the one in effect, which is the state worth seeing
                        // most.
                        (o.unavailable
                          ? "text-disabled line-through"
                          : o.current
                            ? "text-ink"
                            : "text-muted")
                      }
                    >
                      {o.label}
                    </span>
                    <span className="flex-1" />
                    <span
                      className={
                        "shrink-0 font-mono text-[10px] " +
                        // Refused beats current here too: the accent is reserved
                        // for actions, selection and live state (CLAUDE.md), and
                        // "unavailable ✓" in violet is a refusal dressed as one.
                        (o.unavailable
                          ? "text-disabled"
                          : o.current
                            ? "text-accent"
                            : "text-disabled")
                      }
                    >
                      {o.current ? `${o.note} ✓` : o.note}
                    </span>
                  </span>
                  {/* THE PROVIDER'S OWN REASON, which the note's one word throws
                      away: "no longer available to new users" is the difference
                      between a mystery and a fact. In the row rather than a
                      `title`, so it is not hover-only and rides into the row's
                      accessible name. */}
                  {o.unavailable && (
                    <span className="mt-1 font-mono text-[10px] leading-[1.5] text-disabled">
                      {o.unavailable}
                    </span>
                  )}
                </div>
              );
            })}
          </div>

          {effortLevels.length > 0 && (
            <>
              <div className="px-2.5 pb-2 pt-3 text-[10px] font-medium tracking-[.04em] text-faint">
                Effort
              </div>
              <div
                ref={effortRef}
                role="group"
                aria-label="How thorough Addison should be"
              >
                {effortLevels.map((level) => {
                  const active = activeEffort === level.id;
                  return (
                    <button
                      key={level.id}
                      type="button"
                      aria-pressed={active}
                      onClick={() => {
                        if (!usingPlaceholder) onSelectEffort(level.id);
                      }}
                      className="flex w-full items-baseline gap-2.5 rounded-[4px] px-2.5 py-[7px] text-left transition-colors hover:bg-line"
                    >
                      <span
                        className={
                          "min-w-0 truncate font-mono text-[10.5px] " +
                          (active ? "text-ink" : "text-muted")
                        }
                      >
                        {level.label}
                      </span>
                      <span className="flex-1" />
                      {active && (
                        <span className="shrink-0 font-mono text-[10px] text-accent">✓</span>
                      )}
                    </button>
                  );
                })}
              </div>
            </>
          )}

          {/* The second line is the same honesty the Settings popup carries: a
              provider's catalogue lists models a given key may not be allowed to
              call, and which ones is not knowable until one is called. One line
              under the list beats a guess on every row. */}
          <div className="mt-1.5 border-t border-line px-2.5 pb-1 pt-2 font-mono text-[10px] text-disabled">
            picked per message · default in Settings
            <div className="pt-1">not every model works with every key</div>
          </div>
        </div>
      )}
    </span>
  );
}
