// Highlight → Ask / Explain — the selection popover in the chat thread
// (KNOWN-GAPS, "Feature suggestions judged 2026-08-09", first bullet).
//
// Select a sentence in a message you have already read and two plain actions
// appear beside it: **Ask** (you type the question) and **Explain** (the same
// mechanism with the question already written). ONE FEATURE, TWO LABELS — both
// go through the same seed call, and neither sends anything. The selection is
// written into the composer as a markdown blockquote with a blank line after it,
// and the person types (or edits) and presses Send themselves.
//
// It is deliberately NOT a second chat surface. A popover that quotes into the
// one thread mints no state to manage, restore or explain; a little window with
// its own answer in it forks the correspondence. Read-only and frontend-only:
// no tool, no gate, no registry, no IPC, and therefore the same in every
// profile. Quoting is the PERSON quoting into their OWN message, so there is no
// screening question here — this text is already on their screen.
//
// THREE THINGS THAT ARE DELIBERATE, not incidental:
//
//   * It offers itself on SETTLED text only. A row that is still streaming (or
//     still resolving out of the scramble) carries random glyphs in its tail,
//     and a glyph is not something anybody meant to quote. Membership is read
//     from `data-ask-selectable`, which MessageRow puts on a body only when that
//     body is finished — the row's own state, never a guess at the characters.
//   * The selection string is captured the moment the popover appears, not read
//     back when a button is pressed. The thread scrambles some of its own text
//     on click (`installScrambleClickHandler`), rows re-render while another
//     answer streams, and a browser drops the selection for reasons of its own —
//     what is quoted must be what was on screen when the person let go.
//   * One message, whole. A range whose common ancestor is not inside a single
//     selectable body — two messages, a message plus the header, a sender label
//     — offers nothing at all. Failing toward silence is right here: the
//     alternative is quoting text the person did not point at.
//
// The panel itself is floating chrome, which in the v4 direction is the one
// element still allowed a border and a radius: `panel` fill, `rail` hairline,
// 6px radius, `shadow-popover`. Its fadeRise is killed by the global
// prefers-reduced-motion rule in styles.css, like every other animation.

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";

/** The canned question Explain writes for you. Plain words, and it asks for
 * plain words back — the whole point of the feature is the reader who cannot
 * phrase the follow-up (personas Mira, 54 and Petr, 68). */
export const EXPLAIN_QUESTION = "What does this mean, in plain language?";

/** Gap between the selection and the panel, and from the thread's edges. */
const GAP_PX = 8;
const EDGE_PX = 8;

/**
 * The selection, as a markdown blockquote with a blank line after it — so
 * whatever the person types next is their own sentence and not part of the
 * quote. Every line is quoted, including the blank ones inside the selection
 * (bare `>`, since a quote marker followed by a space and nothing else is just
 * trailing whitespace in their draft). Line endings are normalised; leading
 * blank lines and trailing whitespace from a sloppy drag are dropped, but the
 * indentation of the first quoted line is not — it may be code.
 */
export function quoteForComposer(selection: string): string {
  const text = selection.replace(/\r\n?/g, "\n").replace(/^\n+/, "").replace(/\s+$/, "");
  const quoted = text
    .split("\n")
    .map((line) => (line.length > 0 ? `> ${line}` : ">"))
    .join("\n");
  return `${quoted}\n\n`;
}

interface Anchor {
  /** Viewport rect of the selection, and of the thread it must stay inside. */
  top: number;
  bottom: number;
  centerX: number;
  bounds: { top: number; bottom: number; left: number; right: number };
}

interface Offer {
  text: string;
  anchor: Anchor;
}

interface Props {
  /** The thread's scroll container: what the selection must live inside, and
   * what the panel is clamped to. */
  containerRef: RefObject<HTMLElement | null>;
  /** Writes into the composer (App's one-shot `composerSeed`). Nothing sends. */
  onSeed: (text: string) => void;
  /** Which conversation is on screen — switching it drops any offer. */
  conversationKey?: string | null;
}

export function SelectionAsk({ containerRef, onSeed, conversationKey }: Props) {
  const [offer, setOffer] = useState<Offer | null>(null);
  const panelRef = useRef<HTMLDivElement | null>(null);
  // True between mousedown and mouseup: a drag in progress is not a selection
  // yet, and evaluating every `selectionchange` frame of it would fly the panel
  // around under the pointer.
  const draggingRef = useRef(false);

  const evaluate = useCallback(() => {
    const container = containerRef.current;
    const selection = typeof window !== "undefined" ? window.getSelection() : null;
    if (!container || !selection || selection.rangeCount === 0 || selection.isCollapsed) {
      setOffer(null);
      return;
    }
    // The SELECTION's text, not the range's: a range concatenates text nodes, so
    // a highlight across two paragraphs of a rendered answer would come back as
    // one run-on line. What the person sees as two lines must quote as two.
    const text = selection.toString();
    if (text.trim().length === 0) {
      setOffer(null);
      return;
    }
    const range = selection.getRangeAt(0);
    // `commonAncestorContainer` is the deepest node holding the WHOLE range, so
    // finding a selectable body above it proves both ends are inside that one
    // body. Nothing else needs checking, and nothing else may be assumed.
    const node = range.commonAncestorContainer;
    const start = node.nodeType === Node.ELEMENT_NODE ? (node as Element) : node.parentElement;
    const body = start?.closest("[data-ask-selectable]") ?? null;
    if (!body || !container.contains(body)) {
      setOffer(null);
      return;
    }
    const rect = range.getBoundingClientRect();
    const bounds = container.getBoundingClientRect();
    setOffer({
      text,
      anchor: {
        top: rect.top,
        bottom: rect.bottom,
        centerX: rect.left + rect.width / 2,
        bounds: {
          top: bounds.top,
          bottom: bounds.bottom,
          left: bounds.left,
          right: bounds.right,
        },
      },
    });
  }, [containerRef]);

  // Mouse: decide once, on release. Keyboard (Shift+arrows) never sends a
  // mouseup, so `selectionchange` covers it — and covers a selection cleared by
  // a click elsewhere, which is what dismisses the panel on a collapse.
  useEffect(() => {
    function onMouseDown(event: MouseEvent) {
      const target = event.target;
      // A press ON the panel is the person reaching for a button, not the start
      // of a new selection. (The buttons also block the default so the browser
      // never clears the selection out from under them.)
      if (target instanceof Node && panelRef.current?.contains(target)) return;
      draggingRef.current = true;
      setOffer(null);
    }
    function onMouseUp() {
      draggingRef.current = false;
      evaluate();
    }
    function onSelectionChange() {
      if (draggingRef.current) return;
      evaluate();
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setOffer(null);
    }
    // A drag released outside the window may never deliver its mouseup, and a
    // `dragging` flag left standing would kill the keyboard route for the rest
    // of the session. Losing focus ends any drag this component cares about.
    function onBlur() {
      draggingRef.current = false;
    }
    document.addEventListener("mousedown", onMouseDown);
    document.addEventListener("mouseup", onMouseUp);
    document.addEventListener("selectionchange", onSelectionChange);
    document.addEventListener("keydown", onKeyDown);
    window.addEventListener("blur", onBlur);
    return () => {
      document.removeEventListener("mousedown", onMouseDown);
      document.removeEventListener("mouseup", onMouseUp);
      document.removeEventListener("selectionchange", onSelectionChange);
      document.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("blur", onBlur);
    };
  }, [evaluate]);

  // Anything that moves the text moves the panel away from it: the thread's own
  // scroll, a page scroll, a resize. The position is a snapshot, so the honest
  // answer is to stop offering rather than to chase.
  useEffect(() => {
    if (!offer) return;
    const dismiss = () => setOffer(null);
    // Capture phase on `window`: a scroll event does not bubble, so this is what
    // hears the THREAD's own scroll container — including the programmatic
    // scroll the thread performs when an answer lands under the reader.
    window.addEventListener("scroll", dismiss, true);
    window.addEventListener("resize", dismiss);
    return () => {
      window.removeEventListener("scroll", dismiss, true);
      window.removeEventListener("resize", dismiss);
    };
  }, [offer]);

  // Another conversation is another thread: whatever was selected is gone.
  useEffect(() => {
    setOffer(null);
  }, [conversationKey]);

  // Placed after measuring, above the selection when there is room and below it
  // otherwise, clamped to the thread — so it never covers what was selected and
  // never hangs off the column. Written to the node rather than to state: this
  // is layout, and a state round trip here would paint the panel in the wrong
  // place for one frame.
  useLayoutEffect(() => {
    const el = panelRef.current;
    if (!el || !offer) return;
    const { anchor } = offer;
    const width = el.offsetWidth;
    const height = el.offsetHeight;
    const minLeft = anchor.bounds.left + EDGE_PX;
    const maxLeft = Math.max(minLeft, anchor.bounds.right - width - EDGE_PX);
    const left = Math.min(Math.max(anchor.centerX - width / 2, minLeft), maxLeft);
    let top = anchor.top - height - GAP_PX;
    if (top < anchor.bounds.top + EDGE_PX) top = anchor.bottom + GAP_PX;
    el.style.left = `${left}px`;
    el.style.top = `${top}px`;
  }, [offer]);

  if (!offer) return null;

  // Read out here, not inside the handlers: what gets quoted is what was on
  // screen when the panel appeared — see the header note about capture.
  const selected = offer.text;

  function seed(question: string) {
    const quoted = quoteForComposer(selected);
    onSeed(question ? `${quoted}${question}` : quoted);
    setOffer(null);
    // The quote is in the composer now; leaving the highlight behind would say
    // the thread is still the thing being acted on.
    window.getSelection()?.removeAllRanges();
  }

  return (
    <div
      ref={panelRef}
      data-testid="selection-ask"
      role="group"
      aria-label="Ask about the text you selected"
      // Fixed, so the panel does not need a positioned ancestor inside a scroll
      // container that would clip it. Any scroll dismisses it (above).
      // `z-30` is the composer menu's layer, deliberately: above the thread, and
      // BELOW the z-40 overlay layer, so a drawer or modal is never underneath a
      // popover about text it is covering.
      className="fixed z-30 flex animate-[fadeRise_.15s_ease_both] items-center gap-1 rounded-[6px] border border-rail bg-panel px-1 py-1 shadow-popover"
      style={{ top: 0, left: 0 }}
    >
      <SelectionAction
        label="Ask"
        description="Quote this in the message box so you can ask about it"
        onPick={() => seed("")}
      />
      <SelectionAction
        label="Explain"
        description="Quote this and ask Addison what it means"
        onPick={() => seed(EXPLAIN_QUESTION)}
      />
    </div>
  );
}

function SelectionAction({
  label,
  description,
  onPick,
}: {
  label: string;
  description: string;
  onPick: () => void;
}) {
  return (
    <button
      type="button"
      aria-label={description}
      title={description}
      // The press must not clear the selection before the click reads it, and it
      // must not start a new one either.
      onMouseDown={(event) => event.preventDefault()}
      onClick={onPick}
      className="rounded-[4px] px-2 py-1 text-[12px] text-accent transition-colors hover:bg-line hover:text-ink max-md:min-h-[44px]"
    >
      {label}
    </button>
  );
}
