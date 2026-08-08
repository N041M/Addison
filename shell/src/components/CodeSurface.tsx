// The review surface — the Developer/Custom screen that shows what Addison changed
// and lets you put one file back. (Phase-3 review-surface plan Build §4.)
//
// The harness could already read and edit a real project directory. What it could
// not do is SHOW you what it did: the only evidence of an edit was a one-line
// Activity Panel entry naming a basename, and the only recovery was a LIFO "Undo
// last action" button. A person cannot judge whether they need to roll back a
// change they were never shown. This is that surface — a flow and trust layer, not
// a new execution surface. It adds no model capability whatsoever.
//
// SHAPE. Two columns: what Addison changed and what is on disk (left), and the one
// thing you picked (right). Below the md breakpoint there is no room for two, so
// the left column becomes a drawer through the same `MobileDrawer` the sidebar
// uses — a structural swap, never a second layout.
//
// It is NOT a `<Surface>`: that component is a 580px centred reading column, and a
// two-pane diff will not survive inside one. It keeps the two contracts App's view
// machine actually depends on — the `SURFACE_ID` root and `data-surf` text — so
// entering and leaving animate exactly like every other screen.
//
// WHAT IS NOT HERE, on purpose: typing into a file, saving, a terminal, a run
// button, hunk-level revert, and any language service (no completions, no
// diagnostics). "Editing" is deferred, not abandoned.
//
// NOTHING ON THIS SCREEN IS A BOUNDARY. Every read is refused core-side outside
// the Developer profile and outside a currently-trusted folder, on every single
// call; the dimming and the disabled controls below are HONESTY about what will
// happen, never the thing that makes it happen.

import { useEffect, useState, type ReactNode } from "react";
import type { WorkspaceEdit, WorkspaceEntry } from "../types/protocol";
import type { WorkspaceRoot } from "../types/ui";
import type { CodeReviewState } from "../hooks/useCodeReview";
import type { ResolvedTheme } from "../lib/monacoTheme";
import { CodeDiff, CodeViewer } from "./CodeEditor";
import { MobileDrawer } from "./MobileDrawer";
import { RowAction, RowConfirm, SURFACE_ID } from "./Surface";
import { useMediaQuery } from "../hooks/useMediaQuery";

// ---------------------------------------------------------------------------
// Frozen copy. Every sentence here is one somebody reads before deciding whether
// to throw away work, so each is written out rather than composed, and the tests
// assert them byte-for-byte.
// ---------------------------------------------------------------------------

/** An entry that resolves outside the folder that was trusted. The row is dimmed
 * and does nothing; the refusal itself lives in the core. */
const ESCAPES_LINE = "This points outside the folder you trusted.";

/** The warn-before-clobber. Shown in the confirm — never after. */
const ON_DISK_CHANGED_LINE =
  "You've changed this file since Addison did. Reverting will replace what's " +
  "there now with the version from before Addison's first change.";

/** The third state, and it is a real answer: an edit recorded before Addison
 * started hashing what it wrote, a file the shell cannot judge, or no shell. */
const CANT_TELL_LINE = "Addison can't tell whether this file changed since.";

/** The honest line for an edit Addison cannot put back. The earlier version is
 * right there on the left, so this degrades into something useful rather than into
 * a button that could only fail.
 *
 * IT NAMES NO CAUSE, and that is the fix rather than an omission. `revertable` is
 * ONE boolean on the wire carrying three different facts — the file is not in the
 * shell's session write ledger (the restart case), the shell could not be reached
 * at all, or the one batch call that answers for every listed edit failed — and the
 * sentence used to assert the first of them for all three. One failed batch printed
 * "Addison changed this before the app was last restarted" under every row on the
 * screen, including files it had changed a minute ago. Until the core can tell the
 * three apart on the wire (docs/KNOWN-GAPS.md records the shape that would), this
 * says only the part that is true in all three: it cannot, and here is what is
 * left. */
const NOT_REVERTABLE_LINE =
  "Addison can't put this file back for you. The earlier version is on the left; " +
  "you can copy it.";

/** Revert is refused while a turn is in flight. It would serialise safely behind
 * the worker anyway — this is about not asking somebody to reason about two
 * things changing the same file at once. */
const BUSY_LINE = "Addison is working right now. You can put this file back when it has finished.";

const NO_CHANGES_LINE = "Addison hasn't changed any files that are still changed.";
const NO_FOLDERS_LINE =
  "Addison isn't working in any folders yet. Choose one in Settings, under " +
  '"Folders Addison may work in".';
const DIR_TRUNCATED_LINE =
  "This folder holds more than Addison is showing — these are the first entries by name.";
const FILE_TRUNCATED_LINE =
  "This file is longer than Addison is showing. This is the beginning of it.";
const NOTHING_PICKED_LINE = "Pick a change or a file to see it here.";
const DISCONNECTED_LINE = "Addison's engine isn't connected, so there is nothing to show yet.";
/** The same fact for the second column, in its own words. Two columns asking two
 * questions get two answers: one sentence repeated verbatim under both labels
 * reads like a rendering accident, and the second column's question ("which
 * folders?") has an answer of its own that this one gives. */
const DISCONNECTED_FOLDERS_LINE =
  "Addison's engine isn't connected, so it can't say which folders it may work in.";
/** A diff whose edit has left the changes list. The pane keeps rendering, because
 * the text is still the last thing Addison actually did to that file and reading it
 * is the point — but the Revert control is gone, and a diff with no control and no
 * sentence would read as current. */
const STALE_DIFF_LINE =
  "This change isn't in Addison's list any more. What's below is how the file " +
  "looked when you opened it.";

interface Props {
  connected: boolean;
  /** The engine/status banners — the surfaces' `pinned` slot, kept by name. */
  pinned?: ReactNode;
  roots: WorkspaceRoot[];
  rootsLoaded: boolean;
  review: CodeReviewState;
  /** Which palette is live. App computes it once and passes it down; this screen
   * never asks the DOM, and there is no observer anywhere. */
  theme: ResolvedTheme;
  /** A turn is in flight. Revert is held while it is. */
  turnWorking: boolean;
  /** "Addison's work" — the Activity Panel, which follows the person here rather
   * than being left behind in a rail that is collapsed to zero width on a surface.
   * The consent card follows too, on App's own fixed layer; neither is duplicated. */
  work?: ReactNode;
}

export function CodeSurface({
  connected,
  pinned,
  roots,
  rootsLoaded,
  review,
  theme,
  turnWorking,
  work,
}: Props) {
  const isNarrow = useMediaQuery("(max-width: 767.98px)");
  const [treeOpen, setTreeOpen] = useState(false);

  // Growing the window past the breakpoint brings the static column back, so a
  // drawer left open would be the same panel twice.
  useEffect(() => {
    if (!isNarrow) setTreeOpen(false);
  }, [isNarrow]);

  // Escape closes the drawer FIRST, and never also leaves the screen. App's
  // window-level handler orders drawer → modal → surface for exactly this reason;
  // this drawer is not one App knows about, so it takes its own key in the CAPTURE
  // phase and stops there — the same thing the anchored model popup does.
  useEffect(() => {
    if (!treeOpen) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      setTreeOpen(false);
    }
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [treeOpen]);

  const panel = (
    <LeftColumn
      connected={connected}
      roots={roots}
      rootsLoaded={rootsLoaded}
      review={review}
      onPicked={() => setTreeOpen(false)}
      // "Addison's work" rides in the left column while there IS one. On a narrow
      // window that column is a drawer, and a live step list inside a closed drawer
      // is the same failure as leaving it in the collapsed rail — so there it moves
      // beside the pane instead. One place either way, never two.
      work={isNarrow ? undefined : work}
    />
  );

  return (
    <div id={SURFACE_ID} className="flex min-h-0 w-full flex-1">
      {!isNarrow && (
        <aside className="no-scrollbar w-[260px] shrink-0 overflow-y-auto border-r border-line pb-6 pr-4 pt-9">
          {panel}
        </aside>
      )}
      <section className="flex min-h-0 min-w-0 flex-1 flex-col pb-6 pt-9 md:pl-6">
        {pinned && <div className="mb-4 flex shrink-0 flex-col gap-2">{pinned}</div>}
        {isNarrow && work && <div className="mb-4 shrink-0">{work}</div>}
        <RightPane
          review={review}
          theme={theme}
          turnWorking={turnWorking}
          isNarrow={isNarrow}
          onOpenTree={() => setTreeOpen(true)}
        />
      </section>
      {isNarrow && (
        <MobileDrawer open={treeOpen} onClose={() => setTreeOpen(false)}>
          <div className="no-scrollbar h-full w-full overflow-y-auto bg-paper px-4 pb-6 pt-[calc(env(safe-area-inset-top)+20px)]">
            {panel}
          </div>
        </MobileDrawer>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Left: what Addison changed, then what is on disk.
// ---------------------------------------------------------------------------

function LeftColumn({
  connected,
  roots,
  rootsLoaded,
  review,
  onPicked,
  work,
}: {
  connected: boolean;
  roots: WorkspaceRoot[];
  rootsLoaded: boolean;
  review: CodeReviewState;
  onPicked: () => void;
  work?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-[26px]">
      <ColumnSection label="Changes Addison made">
        {!connected ? (
          <Quiet>{DISCONNECTED_LINE}</Quiet>
        ) : !review.editsLoaded ? (
          <Quiet>Looking…</Quiet>
        ) : review.editsError ? (
          <Quiet>{review.editsError}</Quiet>
        ) : review.edits.length === 0 ? (
          <Quiet>{NO_CHANGES_LINE}</Quiet>
        ) : (
          <>
            {review.edits.map((edit) => (
              <EditRow
                key={edit.path}
                edit={edit}
                selected={
                  review.selection?.kind === "edit" && review.selection.path === edit.path
                }
                onOpen={() => {
                  review.openEdit(edit.path);
                  onPicked();
                }}
              />
            ))}
            {review.editsTruncated && (
              <Quiet>Addison has changed more files than this list shows.</Quiet>
            )}
          </>
        )}
      </ColumnSection>

      <ColumnSection label="Folders Addison may work in">
        {/* `connected` FIRST, exactly as the column above does it. Gated on
            `rootsLoaded` alone, an engine that is down renders "isn't connected"
            over one column and a permanent "Looking…" under the other — a
            spinner for an answer nobody is coming back with. */}
        {!connected ? (
          <Quiet>{DISCONNECTED_FOLDERS_LINE}</Quiet>
        ) : !rootsLoaded ? (
          <Quiet>Looking…</Quiet>
        ) : roots.length === 0 ? (
          <Quiet>{NO_FOLDERS_LINE}</Quiet>
        ) : (
          roots.map((root) => (
            <DirectoryNode
              key={root.directory}
              directory={root.directory}
              label={root.directory}
              depth={0}
              review={review}
              onPicked={onPicked}
            />
          ))
        )}
      </ColumnSection>

      {work && <div className="shrink-0">{work}</div>}
    </div>
  );
}

function ColumnSection({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="shrink-0 animate-[fadeRise_.35s_ease_both]">
      <div
        data-surf="1"
        className="border-l-2 border-rail pl-3.5 text-[11px] font-medium tracking-[.04em] text-faint"
      >
        {label}
      </div>
      <div className="mt-2.5 flex flex-col">{children}</div>
    </div>
  );
}

/** A sentence in the column — prose, so it wraps and keeps its leading. */
function Quiet({ children }: { children: ReactNode }) {
  return <p className="m-0 py-1.5 pl-3.5 text-[11.5px] leading-[1.55] text-muted">{children}</p>;
}

/** One file Addison changed that is still changed. `relativePath` by default —
 * the whole path only when there is no root for it to be relative to (trust
 * revoked since the write), because a bare basename for a file nobody can place
 * is less useful than the long answer, and the long answer is true. */
function EditRow({
  edit,
  selected,
  onOpen,
}: {
  edit: WorkspaceEdit;
  selected: boolean;
  onOpen: () => void;
}) {
  const note = edit.missing ? "gone" : edit.created ? "new" : edit.writes > 1 ? `${edit.writes}×` : "";
  return (
    <button
      type="button"
      onClick={onOpen}
      title={edit.path}
      aria-label={edit.relativePath}
      aria-current={selected ? "true" : undefined}
      className={
        "flex w-full items-baseline justify-between gap-2 border-l-2 py-1.5 pl-3 text-left text-[12px] transition-colors hover:text-ink max-md:min-h-[44px] " +
        (selected ? "border-accent text-ink" : "border-transparent text-muted")
      }
    >
      <span className="min-w-0 flex-1 truncate">{edit.relativePath}</span>
      {note && <span className="shrink-0 font-mono text-[10px] text-disabled">{note}</span>}
    </button>
  );
}

/** Join a child onto its parent using the separator the parent already uses, so
 * this does not assume POSIX on a platform the shell also targets. */
function joinPath(directory: string, name: string): string {
  const sep = directory.includes("\\") && !directory.includes("/") ? "\\" : "/";
  return directory.endsWith(sep) ? directory + name : directory + sep + name;
}

/**
 * One expandable folder. Expansion-driven and ONE LEVEL per call — there is no
 * depth parameter anywhere in this stack.
 *
 * `.git` and `node_modules` are listed exactly like everything else and simply
 * left collapsed: hiding them would be a lie about what is on disk, and telling
 * the truth about what is on disk is this surface's only value.
 */
function DirectoryNode({
  directory,
  label,
  depth,
  review,
  onPicked,
}: {
  directory: string;
  label: string;
  depth: number;
  review: CodeReviewState;
  onPicked: () => void;
}) {
  const open = review.expanded.includes(directory);
  const listing = review.listings[directory];
  const error = review.listingErrors[directory];
  return (
    <div>
      <TreeRow
        depth={depth}
        glyph={open ? "▾" : "▸"}
        label={label}
        onClick={() => review.toggleDirectory(directory)}
        expanded={open}
      />
      {open && (
        <div>
          {error ? (
            // The refusal AND a way out of it. Without the retry the only route back
            // is to close the folder and open it again — and the folder is already
            // in `expanded`, so the next press CLOSES rather than asking again.
            <div style={{ paddingLeft: 12 + depth * 14 }}>
              <p className="m-0 py-1.5 text-[11.5px] leading-[1.55] text-muted">{error}</p>
              <RowAction
                tone="muted"
                onClick={() => review.retryDirectory(directory)}
                ariaLabel={`Look inside ${label} again`}
              >
                try again
              </RowAction>
            </div>
          ) : !listing ? (
            <Quiet>Looking…</Quiet>
          ) : (
            <>
              {listing.entries.map((entry) => (
                <TreeEntry
                  key={entry.name}
                  entry={entry}
                  parent={directory}
                  depth={depth + 1}
                  review={review}
                  onPicked={onPicked}
                />
              ))}
              {listing.truncated && <Quiet>{DIR_TRUNCATED_LINE}</Quiet>}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function TreeEntry({
  entry,
  parent,
  depth,
  review,
  onPicked,
}: {
  entry: WorkspaceEntry;
  parent: string;
  depth: number;
  review: CodeReviewState;
  onPicked: () => void;
}) {
  const path = joinPath(parent, entry.name);

  // A row that leads out of the trusted folder is DIMMED and does nothing. The
  // boundary is the core's refusal on the follow-up call; this is the honesty that
  // stops somebody clicking it in the first place. It applies to any kind — a
  // link, a folder reached through one, or a row whose target could not be
  // resolved at all, which the core reports as escaping for the same reason.
  if (entry.escapes) {
    return (
      <div data-escapes="" style={{ paddingLeft: 12 + depth * 14 }}>
        <div className="flex items-baseline gap-2 py-1.5 text-[12px] text-disabled">
          <span aria-hidden="true" className="w-3 shrink-0 text-center">
            ↗
          </span>
          <span className="min-w-0 flex-1 truncate line-through">{entry.name}</span>
        </div>
        <p className="m-0 pb-1.5 pl-5 text-[11px] leading-[1.5] text-disabled">{ESCAPES_LINE}</p>
      </div>
    );
  }

  if (entry.kind === "directory") {
    return (
      <DirectoryNode
        directory={path}
        label={entry.name}
        depth={depth}
        review={review}
        onPicked={onPicked}
      />
    );
  }

  // A shortcut that stays inside the trusted folder is shown, and shown as what it
  // is, but is not opened from here: this side deliberately does not know whether
  // it points at a file or a folder, and guessing would put the wrong control on
  // screen. Its target is reachable through the tree by its own name.
  //
  // ...and anything else is labelled "other", not "shortcut". `parseEntryKind`
  // fails closed to `other` for a socket, a device, and for any kind a later core
  // sends that this build has never heard of — none of which is a shortcut, and the
  // badge is the only thing on the row that says what the entry IS.
  if (entry.kind !== "file") {
    const shortcut = entry.kind === "symlink";
    return (
      <div
        className="flex items-baseline gap-2 py-1.5 text-[12px] text-disabled"
        style={{ paddingLeft: 12 + depth * 14 }}
      >
        <span aria-hidden="true" className="w-3 shrink-0 text-center">
          {shortcut ? "↗" : "·"}
        </span>
        <span className="min-w-0 flex-1 truncate">{entry.name}</span>
        <span className="shrink-0 font-mono text-[10px]">{shortcut ? "shortcut" : "other"}</span>
      </div>
    );
  }

  const selected = review.selection?.kind === "file" && review.selection.path === path;
  return (
    <TreeRow
      depth={depth}
      glyph=""
      label={entry.name}
      selected={selected}
      onClick={() => {
        review.openFile(path);
        onPicked();
      }}
    />
  );
}

function TreeRow({
  depth,
  glyph,
  label,
  onClick,
  selected = false,
  expanded,
}: {
  depth: number;
  glyph: string;
  label: string;
  onClick: () => void;
  selected?: boolean;
  expanded?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-expanded={expanded}
      aria-current={selected ? "true" : undefined}
      style={{ paddingLeft: 12 + depth * 14 }}
      className={
        "flex w-full items-baseline gap-2 border-l-2 py-1.5 text-left text-[12px] transition-colors hover:text-ink max-md:min-h-[44px] " +
        (selected ? "border-accent text-ink" : "border-transparent text-muted")
      }
    >
      <span aria-hidden="true" className="w-3 shrink-0 text-center text-[9px] text-disabled">
        {glyph}
      </span>
      <span className="min-w-0 flex-1 truncate">{label}</span>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Right: the one thing you picked.
// ---------------------------------------------------------------------------

function RightPane({
  review,
  theme,
  turnWorking,
  isNarrow,
  onOpenTree,
}: {
  review: CodeReviewState;
  theme: ResolvedTheme;
  turnWorking: boolean;
  isNarrow: boolean;
  onOpenTree: () => void;
}) {
  const { selection } = review;
  const edit =
    selection?.kind === "edit"
      ? review.edits.find((e) => e.path === selection.path)
      : undefined;
  // A DIFF CAN OUTLIVE ITS EDIT. The list is re-read on a revert, on a return to
  // the screen and whenever the core says something changed; when the row for the
  // open diff leaves it, `RevertBlock` disappears and the pane goes on rendering
  // the same before/after as if it were current. Only claimed once the list is
  // actually an answer — before the first one arrives every diff would qualify.
  // It is the DIFF being on screen that makes this worth saying: with a refusal or
  // an "Opening…" in the pane there is nothing claiming to be current.
  const editIsGone =
    selection?.kind === "edit" &&
    Boolean(review.diff) &&
    review.editsLoaded &&
    !review.editsError &&
    !edit;

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="mb-3 flex shrink-0 items-baseline gap-3">
        {isNarrow && (
          <RowAction onClick={onOpenTree} ariaLabel="Show changes and files" tone="muted">
            ☰ changes
          </RowAction>
        )}
        <span data-surf="1" className="min-w-0 flex-1 truncate text-[13px] text-ink-soft">
          {selection ? nameOf(selection.path) : "Review"}
        </span>
        {selection && (
          <span className="shrink-0 font-mono text-[10px] text-disabled">
            {selection.kind === "edit" ? "before · after" : "read only"}
          </span>
        )}
      </div>

      {edit && (
        <RevertBlock
          edit={edit}
          busy={review.reverting === edit.path}
          turnWorking={turnWorking}
          error={review.revertError}
          onRevert={() => review.revert(edit.path)}
        />
      )}

      {editIsGone && (
        <p
          data-stale-diff=""
          className="m-0 mb-3 shrink-0 text-[12px] leading-[1.55] text-muted"
        >
          {STALE_DIFF_LINE}
        </p>
      )}

      {review.revertNotice && !selection && (
        <p className="m-0 mb-3 shrink-0 text-[12px] leading-[1.55] text-ink-soft">
          {review.revertNotice}
        </p>
      )}

      {review.selection?.kind === "file" && review.fileView?.truncated && (
        <p className="m-0 mb-2 shrink-0 text-[11.5px] leading-[1.55] text-muted">
          {FILE_TRUNCATED_LINE}
        </p>
      )}

      <div className="min-h-0 flex-1 overflow-hidden rounded-[5px] border border-line">
        <PaneBody review={review} theme={theme} />
      </div>
    </div>
  );
}

function PaneBody({ review, theme }: { review: CodeReviewState; theme: ResolvedTheme }) {
  const { selection } = review;
  if (!selection) return <Centered>{NOTHING_PICKED_LINE}</Centered>;
  if (review.paneError) return <Centered>{review.paneError}</Centered>;
  if (review.paneBusy) return <Centered>Opening…</Centered>;
  if (selection.kind === "file") {
    if (!review.fileView) return <Centered>Opening…</Centered>;
    return (
      <CodeViewer
        path={review.fileView.path}
        text={review.fileView.content}
        theme={theme}
        ariaLabel={`${nameOf(review.fileView.path)}, read only`}
      />
    );
  }
  if (!review.diff) return <Centered>Opening…</Centered>;
  return (
    <CodeDiff
      path={review.diff.path}
      before={review.diff.before}
      after={review.diff.after}
      theme={theme}
      ariaLabel={`${nameOf(review.diff.path)}, before and after Addison's changes`}
    />
  );
}

function Centered({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full items-center justify-center bg-panel px-6 text-center text-[12px] leading-[1.55] text-muted">
      {children}
    </div>
  );
}

/**
 * The Revert control and everything that has to be true before somebody presses it.
 *
 * Three states, and only the first has a button:
 *   * revertable → a two-step inline confirm (never `window.confirm()`, which
 *     cannot carry the consequence copy — and the copy is the point). The confirm
 *     is where the warn-before-clobber sentence lives, because a warning after the
 *     press is not a warning.
 *   * not revertable → the honest line. The BEFORE text is on the left, so the
 *     screen degrades into something useful rather than a dead button.
 *   * a turn in flight → the control is held, and says so.
 *
 * Reverting settles the WHOLE unreverted chain for that file at once, down to the
 * state it was in before Addison's first change — which is exactly what the left
 * pane is showing. That is not a nicety: reverting one link of a chain would leave
 * the LIFO undo stack able to resurrect content somebody deliberately reverted away
 * from. The core owns that semantics; this screen just never implies otherwise,
 * which is why there is no per-write control anywhere on it.
 */
function RevertBlock({
  edit,
  busy,
  turnWorking,
  error,
  onRevert,
}: {
  edit: WorkspaceEdit;
  busy: boolean;
  turnWorking: boolean;
  error: string | null;
  onRevert: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const name = nameOf(edit.path);

  // A different file is a different question. Without this, opening a second edit
  // while a confirm was open would show the first file's confirm over the second
  // file's diff.
  useEffect(() => setConfirming(false), [edit.path]);

  if (!edit.revertable) {
    return (
      <p
        data-not-revertable=""
        className="m-0 mb-3 shrink-0 text-[12px] leading-[1.55] text-muted"
      >
        {NOT_REVERTABLE_LINE}
      </p>
    );
  }

  return (
    <div className="mb-3 shrink-0">
      <div className="flex items-baseline gap-3">
        <span className="min-w-0 flex-1 truncate text-[12px] text-ink-soft">
          {edit.created
            ? "Addison created this file."
            : `Addison changed this file${edit.writes > 1 ? ` ${edit.writes} times` : ""}.`}
        </span>
        <RowAction
          onClick={() => setConfirming(true)}
          disabled={confirming || busy || turnWorking}
          ariaLabel={`Put ${name} back`}
        >
          {busy ? "putting it back…" : "put it back"}
        </RowAction>
      </div>
      {turnWorking && !confirming && (
        <p className="m-0 mt-1.5 text-[11.5px] leading-[1.55] text-muted">{BUSY_LINE}</p>
      )}
      {confirming && (
        <RowConfirm
          confirmLabel="Put it back"
          busy={busy || turnWorking}
          onConfirm={onRevert}
          onCancel={() => setConfirming(false)}
        >
          <span className="block">
            {edit.created
              ? `Addison created ${name}. Putting it back removes the file.`
              : `${name} goes back to how it was before Addison's first change.`}
          </span>
          {edit.onDiskChanged === true && (
            <span className="mt-1.5 block" data-clobber-warning="">
              {ON_DISK_CHANGED_LINE}
            </span>
          )}
          {edit.onDiskChanged === null && (
            <span className="mt-1.5 block" data-cant-tell="">
              {CANT_TELL_LINE}
            </span>
          )}
        </RowConfirm>
      )}
      {error && <p className="m-0 mt-2 text-[12px] leading-[1.55] text-muted">{error}</p>}
    </div>
  );
}

/** The file's own name. Full paths belong in `title` attributes, never in a
 * heading — a sentence with somebody's home directory in it ends up in
 * screenshots. */
function nameOf(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).pop() ?? path;
}
