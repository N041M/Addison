// The two Monaco panes of the review surface: a read-only file viewer and a
// before/after diff. (Phase-3 review-surface plan §4 "Monaco loading" and §5.)
//
// Monaco arrives with somebody else's theme, somebody else's keybindings and
// somebody else's idea of what an editor is for. Everything in this file is about
// narrowing it back down to the one thing this screen does: SHOW you what Addison
// changed. There is no typing, no saving, no terminal, no run button — the whole
// wave adds zero new execution surface and zero new model capability, which is
// what makes it shippable at this size.
//
// TWO OF THE OPTIONS BELOW ARE SECURITY-RELEVANT, not cosmetic, and both are
// called out at the code rather than buried in a list:
//
//   * `links: false` — Monaco's link detector makes URLs in a file clickable and
//     hands them to its `openerService`. `Markdown.tsx` states the standing rule
//     that this webview never opens URLs itself. A file Addison edited can contain
//     any URL at all, so this must be off.
//   * `contextmenu: false` — the default menu offers Cut and Paste, and the
//     webview has no clipboard capability to give them.
//
// And `domReadOnly` is not a duplicate of `readOnly`: without it the backing
// textarea stays writable, so IME composition and paste still fire input events
// against a "read-only" editor.
//
// LOADING. A dynamic `import("../lib/monaco")` inside an effect, mirroring
// `MermaidDiagram.tsx` — Monaco is code-split into its own lazy chunk and a person
// who never opens this screen never downloads it. That module is also the mock
// point for every test of this screen: Monaco cannot run in jsdom (real layout,
// `ResizeObserver`, `matchMedia`), so a test renders the surface with the module
// stubbed and asserts the surface, never the editor's internals.

import { useEffect, useRef, useState } from "react";
import type { editor } from "monaco-editor/editor/editor.api";
import {
  CODE_FONT_FAMILY,
  CODE_FONT_SIZE,
  CODE_LINE_HEIGHT,
  applyMonacoTheme,
  type ResolvedTheme,
} from "../lib/monacoTheme";

type MonacoModule = typeof import("../lib/monaco");

/** Shared options. Everything here is either a narrowing or the skin. */
const BASE_OPTIONS: editor.IStandaloneEditorConstructionOptions = {
  readOnly: true,
  // See the header: `readOnly` alone leaves the backing textarea writable.
  domReadOnly: true,
  // The panes live in a flex layout that changes width when the tree collapses to
  // a drawer, so Monaco measures itself rather than being told.
  automaticLayout: true,
  // The plan says `minimap: false`; Monaco's option is an object, so this is the
  // same decision spelled the way the API spells it.
  minimap: { enabled: false },
  links: false,
  contextmenu: false,
  quickSuggestions: false,
  codeLens: false,
  occurrencesHighlight: "off",
  // A read-only viewer has no caret to park past the end of the file, and the
  // direction does not own big empty space below content.
  scrollBeyondLastLine: false,
  fontFamily: CODE_FONT_FAMILY,
  fontSize: CODE_FONT_SIZE,
  lineHeight: CODE_LINE_HEIGHT,
  // The gutter is the one piece of chrome a file viewer earns: it is how a person
  // says "line 210" to somebody else.
  lineNumbers: "on",
  // No vertical hairline down the middle of the text.
  renderLineHighlight: "line",
  overviewRulerBorder: false,
  scrollbar: { verticalScrollbarSize: 10, horizontalScrollbarSize: 10 },
};

/**
 * Load the editor module once per session. Returns the module, or `null` while it
 * is still arriving; `failed` is a real state and not a spinner that never stops —
 * the callers fall back to plain text, which is readable rather than blank.
 */
function useMonacoModule(): { mod: MonacoModule | null; failed: boolean } {
  const [mod, setMod] = useState<MonacoModule | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let cancelled = false;
    import("../lib/monaco")
      .then((m) => {
        if (!cancelled) setMod(m);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return { mod, failed };
}

/** The quiet placeholder while the chunk arrives. Same voice as the diagram's. */
function Loading({ label }: { label: string }) {
  return (
    <div className="flex h-full items-center justify-center font-mono text-[10.5px] text-disabled">
      {label}
    </div>
  );
}

/**
 * The plain-text fallback, used when the editor chunk cannot be loaded at all.
 *
 * Not a degraded experience so much as an honest one: the text is the thing the
 * person came for, and a blank pane with a spinner would be the app pretending it
 * still might work. Styled off the same tokens as the editor, so it is recognisably
 * the same surface.
 */
function PlainText({ text }: { text: string }) {
  return (
    <pre className="no-scrollbar m-0 h-full overflow-auto bg-panel p-3 font-mono text-[12px] leading-[19px] text-ink">
      {text}
    </pre>
  );
}

interface ViewerProps {
  /** The resolved path. Drives the language, and nothing else. */
  path: string;
  text: string;
  /** Which palette is live. App computes it; this never asks the DOM itself. */
  theme: ResolvedTheme;
  /** The accessible name — a pane of code with no name is unusable by screen reader. */
  ariaLabel: string;
}

export function CodeViewer({ path, text, theme, ariaLabel }: ViewerProps) {
  const host = useRef<HTMLDivElement>(null);
  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null);
  const { mod, failed } = useMonacoModule();

  useEffect(() => {
    if (!mod || !host.current) return;
    const instance = mod.default.editor.create(host.current, BASE_OPTIONS);
    editorRef.current = instance;
    return () => {
      // The model first: an editor disposed with a model still attached leaks the
      // text buffer for as long as the session lasts, and this screen opens a new
      // one per click.
      instance.getModel()?.dispose();
      instance.dispose();
      editorRef.current = null;
    };
  }, [mod]);

  // A new file (or new bytes) is a new model rather than a `setValue`: the model
  // carries the LANGUAGE, and a viewer that keeps python highlighting on a
  // markdown file is worse than one with no highlighting at all.
  useEffect(() => {
    const instance = editorRef.current;
    if (!mod || !instance) return;
    const previous = instance.getModel();
    instance.setModel(mod.default.editor.createModel(text, mod.languageForPath(path)));
    previous?.dispose();
  }, [mod, path, text]);

  useEffect(() => {
    editorRef.current?.updateOptions({ ariaLabel });
  }, [ariaLabel]);

  // Redefined, not merely re-selected: the hex values are baked at define time, so
  // `setTheme` alone would leave somebody who flips Appearance reading the old
  // palette until they reopened the file.
  useEffect(() => {
    if (mod) applyMonacoTheme(mod.default, theme);
  }, [mod, theme]);

  if (failed) return <PlainText text={text} />;
  return (
    <div className="relative h-full w-full">
      {!mod && <Loading label="opening…" />}
      <div ref={host} className="h-full w-full" data-code-viewer="" />
    </div>
  );
}

interface DiffProps {
  path: string;
  /** What was there before Addison's FIRST unreverted change — where Revert lands. */
  before: string;
  /** What is there NOW, including anything the person changed since. */
  after: string;
  theme: ResolvedTheme;
  ariaLabel: string;
}

export function CodeDiff({ path, before, after, theme, ariaLabel }: DiffProps) {
  const host = useRef<HTMLDivElement>(null);
  const editorRef = useRef<editor.IStandaloneDiffEditor | null>(null);
  const { mod, failed } = useMonacoModule();

  useEffect(() => {
    if (!mod || !host.current) return;
    const instance = mod.default.editor.createDiffEditor(host.current, {
      ...BASE_OPTIONS,
      // The left pane is a stored prior state; it is not editable in any sense.
      originalEditable: false,
      // The window is 1000×720 with a 640px floor, and side-by-side needs about
      // twice a usable pane: at 1000px, minus the sidebar and the tree, there is
      // ~500px, i.e. two 250px columns. Monaco has this exact switch built in, so
      // it is used rather than hand-rolled against a media query that would then
      // disagree with the editor's own idea of its width.
      renderSideBySideInlineBreakpoint: 700,
      useInlineViewWhenSpaceIsLimited: true,
    });
    editorRef.current = instance;
    return () => {
      const model = instance.getModel();
      model?.original.dispose();
      model?.modified.dispose();
      instance.dispose();
      editorRef.current = null;
    };
  }, [mod]);

  useEffect(() => {
    const instance = editorRef.current;
    if (!mod || !instance) return;
    const language = mod.languageForPath(path);
    const previous = instance.getModel();
    instance.setModel({
      original: mod.default.editor.createModel(before, language),
      modified: mod.default.editor.createModel(after, language),
    });
    previous?.original.dispose();
    previous?.modified.dispose();
  }, [mod, path, before, after]);

  useEffect(() => {
    editorRef.current?.updateOptions({ ariaLabel });
  }, [ariaLabel]);

  useEffect(() => {
    if (mod) applyMonacoTheme(mod.default, theme);
  }, [mod, theme]);

  if (failed) {
    // Two stacked plain panes rather than one: without Monaco there is no diff to
    // compute, and showing only "after" would hide the thing Revert lands on.
    return (
      <div className="flex h-full min-h-0 flex-col">
        <div className="min-h-0 flex-1 overflow-hidden border-b border-line">
          <PlainText text={before} />
        </div>
        <div className="min-h-0 flex-1 overflow-hidden">
          <PlainText text={after} />
        </div>
      </div>
    );
  }
  return (
    <div className="relative h-full w-full">
      {!mod && <Loading label="opening…" />}
      <div ref={host} className="h-full w-full" data-code-diff="" />
    </div>
  );
}
