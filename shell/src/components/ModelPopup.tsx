// The anchored model popup — Settings → "Where Addison thinks" → the Cloud model
// row's "change" (docs/design-brief-dark, "Model select popup"; prototype.html
// ~172–182 + `modelPop*` in renderVals ~627–635).
//
// A fixed-position 270px panel that opens AT THE CLICK, macOS-select style: the
// SELECTED row lands under the pointer, so changing your mind is a small
// movement rather than a hunt. That invariant is the whole reason this panel
// positions itself at all, and it is why the vertical placement is MEASURED
// rather than computed from the selected row's index: the list is a folder tree,
// so company and family rows sit between the top of the panel and that row and
// index × row-height stopped describing where anything is drawn. The panel
// renders once with a provisional top, reads the selected row's real offset
// inside itself, and sets the final top before paint.
//
// THE LIST IS A FOLDER TREE (owner decision 2026-08-07 — company → family →
// model, one folder open at a time). `lib/modelGroups.ts` owns the shape and the
// reasoning; the composer's menu draws the same rows from the same engine.
//
// THE KEYBOARD IS THE COMPOSER MENU'S, DELIBERATELY THE SAME ONE (ModelSelector,
// which owns the pattern's reasoning): one tab stop for the whole tree, the
// cursor carried by `aria-activedescendant` resolved against the rows this
// render actually drew, Up/Down/Home/End over what is drawn, Right to open a
// folder, Left to close it or climb, Enter/Space to act. Two panels drawing one
// tree may not have two idioms — a person who has learnt this menu in the
// composer has learnt it here. Focus moves into the tree when the panel opens
// and goes back to the control that opened it when the panel is finished with.
//
// ROWS ARE THE REAL CATALOG, exactly as in the composer's menu (ModelSelector):
// cloud models from the connected providers plus whatever is set up under the
// local role. `free` is never inferred — the note says "free" only when the core
// itself flagged the model, because no cloud model may claim to cost nothing on
// the frontend's authority (CLAUDE.md, Phase-2 step 4).

import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import { isMotionEnabled } from "../lib/scramble";
import {
  initialFolderState,
  modelListRows,
  rowRenderKey,
  toggleCompany,
  toggleFamily,
  type FolderState,
  type ModelListRow,
} from "../lib/modelGroups";

/** Panel geometry — the prototype's numbers, kept as named constants because the
 * positioning maths reads as nonsense without them. */
const PANEL_WIDTH = 270;
/** How far left of the click the panel's left edge sits (prototype: x − 250). */
const ANCHOR_INSET = 250;
/** Never closer than this to any viewport edge. */
const EDGE_MARGIN = 12;
/** Left padding per tree level, indexed by `level - 1` — levels are 1-based. */
const INDENT = ["pl-3", "pl-5", "pl-7"];

export interface ModelPopupOption {
  /** Unique across cloud + local (the role is part of it for local rows). */
  key: string;
  /** The model id, which is what the family folder is derived from. */
  id?: string;
  label: string;
  /**
   * Which company this model comes from — "Anthropic", "Google", "On this
   * computer". Models are drawn inside a folder per company, so a picker holding
   * four providers' models opens as four rows and one open drawer.
   */
  group?: string;
  /** "quality" | "free" | "local" — derived from the CORE's flags, never guessed. */
  note: string;
  /**
   * The provider's own words for refusing this model, when the core has watched
   * it happen ("no longer available to new users"). Carried as the SENTENCE
   * rather than a flag: the row is struck through either way, and the reason is
   * the only part that tells a person what to do about it.
   */
  unavailable?: string;
  selected: boolean;
  onPick: () => void;
}

export interface PopupAnchor {
  /** The trigger's right edge, in viewport coordinates. */
  x: number;
  /** The trigger's vertical centre, in viewport coordinates. */
  y: number;
}

/** How long the popup takes to fade out. Matches the .12s it fades in on. */
const EXIT_MS = 120;

/**
 * The panel's top edge: the click point, less however far down the panel the
 * selected row's centre sits, held inside the viewport.
 *
 * Pure and exported because it is the one piece of this component that can be
 * checked without a browser — jsdom lays nothing out, so the real measurement
 * always reads zero there, and an arithmetic error would go unseen until someone
 * opened the app.
 *
 * `selectedCenter` is measured, not derived from a row count: with folders in
 * the list, the selected row's offset has no closed form. When the panel is
 * taller than the viewport allows, the lower bound overtakes the upper one and
 * both collapse to the margin — the panel scrolls internally instead of hanging
 * off an edge.
 */
export function popupTop(measurements: {
  anchorY: number;
  selectedCenter: number;
  panelHeight: number;
  viewportHeight: number;
}): number {
  const { anchorY, selectedCenter, panelHeight, viewportHeight } = measurements;
  return clamp(
    anchorY - selectedCenter,
    EDGE_MARGIN,
    Math.max(EDGE_MARGIN, viewportHeight - panelHeight - EDGE_MARGIN),
  );
}

export function ModelPopup({
  anchor,
  options,
  open = true,
  onClose,
  returnFocus,
}: {
  anchor: PopupAnchor;
  options: ModelPopupOption[];
  /**
   * False starts the close animation; the popup unmounts itself when it
   * finishes. The exit lives HERE rather than at the call site because several
   * things close this popup — Escape, an outside click, picking a model, a
   * profile change — and a caller that forgot would put the old behaviour back
   * for that one path. It faded in and then vanished on a frame (reported
   * 2026-07-26: "it just plops out").
   */
  open?: boolean;
  onClose: () => void;
  /**
   * Put focus back on the control that opened the panel. The trigger is a
   * Settings row's button and this panel is floating chrome rendered at the end
   * of the app, so it cannot reach the trigger itself — and without this, focus
   * fell to <body> the moment the tree unmounted and a keyboard user was back at
   * the top of the page.
   */
  returnFocus?: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const selectedRowRef = useRef<HTMLDivElement>(null);
  const isSelected = (o: ModelPopupOption) => o.selected;
  /** What a row IS, across a fold: a model keeps its identity when its folders
   * move, so the keyboard's cursor survives an accordion it did not ask for. */
  const rowKeyOf = (row: ModelListRow<ModelPopupOption>) =>
    row.kind === "option" ? `m:${row.option.key}` : rowRenderKey(row);
  // Which folders are open, re-derived on every OPENING rather than once. The
  // caller keeps this component mounted for the life of the app (App.tsx holds
  // the last anchor so the panel can fade out where it opened), so state taken
  // at mount would be the catalogue as it stood at the FIRST open: a provider
  // that connected since would never be foldered, and the promise that the panel
  // opens on the model in effect would hold once and never again. Derived during
  // render, not in an effect, so the rows are already right when the position
  // below is measured.
  const [folders, setFolders] = useState<FolderState>({});
  // Where the keyboard is. Seeded with the folders, for the same reason.
  const [activeKey, setActiveKey] = useState<string>("");
  const [seededOpen, setSeededOpen] = useState(false);
  if (open !== seededOpen) {
    setSeededOpen(open);
    // Only on the way OPEN: while the panel stays open, the folders a person
    // opened are theirs to keep.
    if (open) {
      setFolders(initialFolderState(options, isSelected));
      const selected = options[Math.max(0, options.findIndex(isSelected))];
      setActiveKey(selected ? `m:${selected.key}` : "");
    }
  }
  function activate(row: ModelListRow<ModelPopupOption>) {
    if (row.kind === "option") return;
    setFolders((prev) =>
      row.kind === "company"
        ? toggleCompany(prev, row.key, options, isSelected)
        : toggleFamily(prev, row.key),
    );
    // The cursor follows the folder that was acted on. It is the one row certain
    // to survive the accordion — opening Google closes Anthropic underneath a
    // cursor that was standing in it.
    setActiveKey(rowRenderKey(row));
  }
  const rows = useMemo(() => modelListRows(options, folders), [options, folders]);
  const rowKeys = rows.map(rowKeyOf);
  // RESOLVED AGAINST WHAT IS DRAWN, every render. `aria-activedescendant` naming
  // an element that is not in the document is the classic failure of this
  // pattern — a screen reader is told the cursor is on nothing — and an
  // accordion closes over the cursor from three directions (its own folder, a
  // parent, a sibling company opening). Deriving the id from the rendered rows
  // makes that unrepresentable rather than a case to remember.
  const activeAt = Math.max(0, rowKeys.indexOf(activeKey));
  const treeId = useId();
  const rowDomId = (i: number) => `${treeId}-row-${i}`;
  // Kept up for the length of the exit, then gone.
  const [exiting, setExiting] = useState(false);
  const wasOpen = useRef(false);
  useEffect(() => {
    if (open) {
      wasOpen.current = true;
      setExiting(false);
      return;
    }
    if (!wasOpen.current) return;
    wasOpen.current = false;
    if (!isMotionEnabled()) return;
    setExiting(true);
    const timer = setTimeout(() => setExiting(false), EXIT_MS);
    return () => clearTimeout(timer);
  }, [open]);
  // Derived rather than state, so the panel exists on the render that opens it.
  const present = open || exiting;
  // The row the click point aims at: the selection, or the first row when
  // nothing is selected yet.
  const anchorIndex = Math.max(
    0,
    options.findIndex((o) => o.selected),
  );

  const [selectedCenter, setSelectedCenter] = useState(0);
  const [height, setHeight] = useState(0);
  // ANCHORED ONCE PER OPEN. Opening a folder while the panel is open changes
  // where the selected row sits, but re-centring on it then would slide the
  // panel out from under the hand that just clicked.
  useLayoutEffect(() => {
    if (!open) return;
    const row = selectedRowRef.current;
    const centre = row ? row.offsetTop + row.offsetHeight / 2 : 0;
    setSelectedCenter(centre);

    // AND IT HAS TO BE ON SCREEN INSIDE THE PANEL. Past the height clamp below
    // the panel is a scrollport, so a catalogue whose open folder runs past the
    // window could open showing rows the selection is not among — a menu that
    // exists to say what is on, opening on a view that does not contain it.
    const panel = ref.current;
    if (panel && row) centreRow(panel, row);
    // The tree, not the panel, takes focus: it is the element carrying the
    // cursor, so a screen reader announces the row the panel just opened on.
    // `preventScroll` for the same reason `centreRow` exists — this panel floats
    // over a Settings surface that scrolls, and it has no business moving it.
    listRef.current?.focus({ preventScroll: true });
  }, [open]);
  // Height is the opposite: re-measured whenever the DRAWN rows change, because
  // the bottom clamp is about the panel as it is right now. Keying this on the
  // option COUNT missed every fold — opening a folder grew the panel without
  // adding an option, so the clamp went on believing it was short and let it run
  // off the bottom of the window. Keyed on what the rows ARE rather than on the
  // array holding them: the caller rebuilds its options on every render of the
  // app, and a fresh array identity would force a synchronous layout read behind
  // every streamed chunk while this panel is open.
  const drawnShape = rows
    .map((r) => (r.kind === "option" ? `o${r.level}${r.option.unavailable ?? ""}` : `f${r.level}`))
    .join("|");
  useLayoutEffect(() => {
    if (!present) return;
    setHeight(ref.current?.offsetHeight ?? 0);
  }, [drawnShape, present]);
  // Walk the cursor's row back into view, inside the panel and nowhere else.
  useEffect(() => {
    if (!open) return;
    const panel = ref.current;
    const row = document.getElementById(rowDomId(activeAt));
    if (panel && row) revealRow(panel, row);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeKey, folders, open]);

  const viewportW = typeof window === "undefined" ? PANEL_WIDTH + 2 * EDGE_MARGIN : window.innerWidth;
  const viewportH = typeof window === "undefined" ? 0 : window.innerHeight;

  const rawX = anchor.x - ANCHOR_INSET;
  const left = clamp(rawX, EDGE_MARGIN, Math.max(EDGE_MARGIN, viewportW - PANEL_WIDTH - EDGE_MARGIN));
  const top = popupTop({
    anchorY: anchor.y,
    selectedCenter,
    panelHeight: height,
    viewportHeight: viewportH,
  });

  /**
   * Hand focus back, but only when this panel is still holding it.
   *
   * A click that landed on some other control has already said where focus
   * belongs, and taking it back would fight the person. Every other way out of
   * here — Escape, picking a model — leaves focus inside the tree that is about
   * to unmount, and that is the case this exists for.
   */
  const releaseFocus = useCallback(() => {
    if (ref.current?.contains(document.activeElement)) returnFocus?.();
  }, [returnFocus]);

  function pick(o: ModelPopupOption) {
    releaseFocus();
    o.onPick();
  }

  // Outside click and Escape both close. Escape is handled here, and stops
  // propagating, so it closes the popup rather than leaving the whole surface.
  useEffect(() => {
    if (!open) return; // a closing popup answers nothing
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        releaseFocus();
        onClose();
      }
    }
    function onKey(e: globalThis.KeyboardEvent) {
      if (e.key === "Escape") {
        e.stopPropagation();
        releaseFocus();
        onClose();
      }
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [onClose, open, releaseFocus]);

  const stepActive = (delta: number) => {
    if (rows.length === 0) return;
    setActiveKey(rowKeys[(activeAt + delta + rows.length) % rows.length]);
  };
  /** The folder row this row sits inside: the first shallower row above it. */
  const parentAt = (at: number) => {
    const level = rows[at]?.level ?? 1;
    for (let i = at - 1; i >= 0; i -= 1) if (rows[i].level < level) return i;
    return at;
  };

  function onTreeKeyDown(e: ReactKeyboardEvent<HTMLDivElement>) {
    // A caller may hand this panel an empty catalogue; App does not, but the
    // cursor has nowhere to be if one ever does.
    const row = rows.length > 0 ? rows[activeAt] : undefined;
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
        // which is the next row drawn. On a model there is nothing to open, so
        // nothing happens — a leaf is where Right stops.
        e.preventDefault();
        if (!row || row.kind === "option") break;
        if (row.open) stepActive(1);
        else activate(row);
        break;
      case "ArrowLeft": {
        e.preventDefault();
        if (!row) break;
        // An open folder closes; anything else climbs to the folder it is in.
        if (row.kind !== "option" && row.open) {
          activate(row);
          break;
        }
        const parent = parentAt(activeAt);
        if (parent !== activeAt) setActiveKey(rowKeys[parent]);
        break;
      }
      case "Home":
        e.preventDefault();
        if (rows.length) setActiveKey(rowKeys[0]);
        break;
      case "End":
        e.preventDefault();
        if (rows.length) setActiveKey(rowKeys[rows.length - 1]);
        break;
      case "Enter":
      case " ":
        e.preventDefault();
        if (!row) break;
        if (row.kind === "option") pick(row.option);
        else activate(row);
        break;
      default:
        break;
    }
  }

  if (!present) return null;

  return (
    <div
      ref={ref}
      style={{ left: `${left}px`, top: `${top}px`, width: `${PANEL_WIDTH}px` }}
      // Out of the a11y tree the moment it starts closing — see ModelSelector.
      aria-hidden={!open}
      // The height clamp keeps a wide-open folder inside the window: one
      // provider contributes twenty-two rows, which is ~900px — taller than a
      // laptop viewport, so no amount of clamping the TOP could have kept the
      // bottom on screen. It scrolls instead. (24px is 2 × EDGE_MARGIN; a
      // Tailwind arbitrary value cannot read the constant, so the two are kept
      // in step by hand.)
      className={
        "no-scrollbar fixed z-50 max-h-[calc(100vh-24px)] overflow-y-auto rounded-popover " +
        "bg-panel px-1 py-1.5 shadow-popover " +
        (open
          ? "animate-[fade_.12s_ease_both]"
          : "pointer-events-none animate-[fade-out_.12s_ease_both]")
      }
    >
      <div
        ref={listRef}
        role="tree"
        // ONE TAB STOP for the whole tree, which is what makes the rows below
        // safe to leave out of the tab order: a twenty-two model catalogue was
        // twenty-two stops inside something announced as a tree, and arrows did
        // nothing. The cursor is `aria-activedescendant` now, and the rows move
        // under it. The stop goes away with the panel — while it fades out it is
        // `aria-hidden`, and a hidden thing that Tab can still reach is worse
        // than no stop at all.
        tabIndex={open ? 0 : -1}
        aria-label="Which cloud model Addison uses by default"
        aria-activedescendant={rows.length ? rowDomId(activeAt) : undefined}
        onKeyDown={onTreeKeyDown}
        className="outline-none"
      >
        {rows.map((row, i) => {
          // A hairline between rows, but never between a folder and what it just
          // opened: that pair reads as one thing, and a rule across it would draw
          // the contents as a separate list.
          const hairline = i > 0 && rows[i - 1].level >= row.level;
          const active = i === activeAt;
          if (row.kind !== "option") {
            return (
              <button
                key={rowRenderKey(row)}
                id={rowDomId(i)}
                type="button"
                // A treeitem, not a heading and not a button to a screen reader: a
                // folder in a tree is a row you can act on, and `aria-expanded` is
                // the part that carries whether it is open.
                role="treeitem"
                aria-level={row.level}
                aria-posinset={row.posinset}
                aria-setsize={row.setsize}
                aria-expanded={row.open}
                tabIndex={-1}
                onClick={() => activate(row)}
                onMouseEnter={() => setActiveKey(rowRenderKey(row))}
                className={
                  "flex w-full items-baseline gap-2 py-2.5 pr-3 text-left font-mono " +
                  "transition-colors hover:text-ink-soft " +
                  INDENT[row.level - 1] +
                  (hairline ? " border-t border-t-line" : "") +
                  (active ? " bg-line" : "") +
                  (row.kind === "company"
                    ? " text-[10.5px] text-muted"
                    : " text-[10px] text-disabled")
                }
              >
                {/* Upper-case is the family LABEL's voice, not the row's: on the
                    whole row it shouted the "collapse" hint too, while the company
                    row above whispered the same word. */}
                <span
                  className={
                    "min-w-0 truncate" +
                    (row.kind === "family" ? " uppercase tracking-wider" : "")
                  }
                >
                  {row.key}
                </span>
                <span className="flex-1" />
                <span className="shrink-0 text-disabled">{row.open ? "collapse" : row.total}</span>
              </button>
            );
          }
          const o = row.option;
          return (
            <div
              key={o.key}
              id={rowDomId(i)}
              // The row the panel positions itself by — see `popupTop`.
              ref={row.index === anchorIndex ? selectedRowRef : undefined}
              role="treeitem"
              aria-level={row.level}
              aria-posinset={row.posinset}
              aria-setsize={row.setsize}
              aria-selected={o.selected}
              onClick={() => pick(o)}
              onMouseEnter={() => setActiveKey(`m:${o.key}`)}
              className={
                "flex cursor-pointer flex-col border-l-2 py-3 pr-3 " +
                "text-[12px] transition-colors hover:text-ink " +
                INDENT[row.level - 1] +
                (hairline ? " border-t border-t-line" : "") +
                (active ? " bg-line" : "") +
                // Refused BEATS selected in the chain, including the accent rail:
                // the rail means "this is what answers", and a model the provider
                // has been watched refusing is the one row that should not be
                // wearing it while it is struck through.
                (o.unavailable
                  ? " border-l-transparent text-disabled"
                  : o.selected
                    ? " border-l-accent text-ink"
                    : " border-l-transparent text-ink-soft")
              }
            >
              <span className="flex w-full items-baseline gap-2.5">
                <span className={"min-w-0 truncate " + (o.unavailable ? "line-through" : "")}>
                  {o.label}
                </span>
                <span className="flex-1" />
                <span
                  className={
                    "shrink-0 font-mono text-[10.5px] " +
                    // Refused beats selected here too. The accent is for actions,
                    // selection and live state (CLAUDE.md) — a note reading
                    // "unavailable" is none of the three, and it wore the accent
                    // on the one row already struck through.
                    (o.unavailable ? "text-disabled" : o.selected ? "text-accent" : "text-muted")
                  }
                >
                  {o.note}
                </span>
              </span>
              {/* THE CORE'S OWN SENTENCE, not the word "unavailable" again. The
                  provider said something specific — "no longer available to new
                  users" — and a strike-through alone leaves a person to guess at
                  it. In the row rather than a `title`, so it is not hover-only:
                  the second line is the same shape the Tools surface uses under a
                  row name, and it rides into the row's accessible name. */}
              {o.unavailable && (
                <span className="mt-1 font-mono text-[10px] leading-[1.5] text-disabled">
                  {o.unavailable}
                </span>
              )}
            </div>
          );
        })}
      </div>
      {/*
        A LISTED MODEL IS NOT A PROMISE, and the panel should say so before the
        turn does. A provider's own catalogue over-promises: Google lists
        `gemini-2.5-flash` advertising the right method, then refuses it with "no
        longer available to new users" — retired for keys made after a cutoff and
        still on the list. Nothing visible distinguished it from a working model.

        Deliberately not a per-row badge: which models a key may use is not
        knowable until one is called, so a mark would be a guess on every row.
        One honest line under the list beats thirty confident ones. The one mark
        that IS per-row — the struck-through refused model above — is not the
        exception to that: it reports a refusal the core watched happen, and says
        nothing at all about the rows beside it.

        Outside the tree: a line of prose is not a `treeitem`, and a tree's
        children are treeitems and groups.
      */}
      <div className="border-t border-t-line px-3 py-2 font-mono text-[10px] text-disabled">
        not every model here works with every key
      </div>
    </div>
  );
}

/** Put a row in the middle of the panel's scrollport, for the row the panel
 * opens on. */
function centreRow(panel: HTMLElement, row: HTMLElement) {
  const overflow = panel.scrollHeight - panel.clientHeight;
  const centre = row.offsetTop + row.offsetHeight / 2;
  panel.scrollTop = overflow > 0 ? clamp(centre - panel.clientHeight / 2, 0, overflow) : 0;
}

/**
 * Bring a row inside the panel's scrollport, moving it no further than it has
 * to — the cursor walking down a long list should not jump the view.
 *
 * `scrollTop` rather than `scrollIntoView` in both of these: this panel is
 * fixed-position over a Settings surface that scrolls, and scrollIntoView walks
 * up the ancestors too — it would drag the page out from under a popup somebody
 * just opened, to reach a row that is already where it needs to be.
 */
function revealRow(panel: HTMLElement, row: HTMLElement) {
  const top = row.offsetTop;
  const bottom = top + row.offsetHeight;
  if (top < panel.scrollTop) panel.scrollTop = top;
  else if (bottom > panel.scrollTop + panel.clientHeight) {
    panel.scrollTop = bottom - panel.clientHeight;
  }
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}
