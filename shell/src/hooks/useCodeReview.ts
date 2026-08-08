// The review surface's state: what Addison changed, what is on disk, and which of
// the two the right-hand pane is showing. (Phase-3 review-surface plan Build §4.)
//
// It owns four things and deliberately no more:
//
//   * the CHANGES list (`workspace.listEdits`) — metadata only, newest first;
//   * the file TREE, one level per expansion (`workspace.listDirectory`). There is
//     no depth parameter anywhere in this stack, because a depth knob is how a full
//     repo walk gets requested by accident;
//   * the right-hand PANE — either one file's text (`workspace.readFile`) or one
//     edit's before/after (`workspace.readEditDiff`);
//   * REVERT (`workspace.revertFile`), which puts a file back to the state it was
//     in before Addison's first still-live change to it.
//
// WHAT IT DOES NOT OWN: the trust boundary. The roots come in as a prop from
// `useWorkspace`, and the core re-checks trust on every single call regardless of
// what this hook believes. Nothing here is a gate; every refusal below is a
// sentence the core wrote, forwarded whole.
//
// THE RACE. Every fetch carries a sequence number and a late answer for a path the
// person has already navigated away from is DROPPED. Without it, clicking three
// files quickly leaves whichever answer happened to arrive last on screen, under
// whichever header arrived first — a viewer showing one file's text under another
// file's name, on the one screen in the app whose entire job is to be exact about
// which file is which.

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  WorkspaceEdit,
  WorkspaceEditDiff,
  WorkspaceFileView,
  WorkspaceListing,
} from "../types/protocol";
import type { WorkspaceRoot } from "../types/ui";
import { ipc, isEngineConnected } from "../ipc/client";

// ---------------------------------------------------------------------------
// The four sentences this hook writes itself. Frozen — the tests pin them
// byte-for-byte.
//
// Every refusal the CORE sends is forwarded whole, and none of those are here.
// These cover the case where the core never answered at all: a rejected call, or a
// press made while the engine is down. There is no sentence to forward then, and
// the alternative — what each of these replaced — is a screen that says nothing
// happened on the one surface whose whole job is to be exact about what did.
// ---------------------------------------------------------------------------

/** The CHANGES list could not be read. Says what is not known rather than letting
 * the column fall through to "Addison hasn't changed any files", which states as
 * fact the one thing a failed call cannot know. The next step is true because of
 * the came-back-to-the-screen re-read further down. */
const EDITS_UNREADABLE =
  "Addison couldn't check which files it has changed just now. Leaving this screen " +
  "and coming back asks again.";

/** One folder could not be listed. No next step in the sentence because there is
 * one on screen: the row renders a "try again" beside it. */
const DIRECTORY_UNREADABLE = "Addison couldn't look inside this folder just now.";

/** A Revert that did not come back. Every sibling in the app authors one of these
 * (`REMOVE_FAILED`, `DISARM_ORPHAN_FAILED`); this is the button where saying
 * nothing costs the most, because the person is watching for a file to change. */
const REVERT_FAILED = "Addison couldn't put that file back just now. You can try again.";

/** ...and a press made with no engine behind it. Kept apart from the one above
 * because it is a different fact and has a different next step. */
const REVERT_DISCONNECTED =
  "Addison's engine isn't connected, so it can't put this file back. Try again in a moment.";

/** What the right pane is showing. The PATH is held separately from the payload so
 * a header can name the file while its text is still arriving. */
export interface CodeSelection {
  kind: "file" | "edit";
  path: string;
}

export interface CodeReviewState {
  // --- what Addison changed ---
  edits: WorkspaceEdit[];
  editsLoaded: boolean;
  editsTruncated: boolean;
  editsError: string | null;
  refreshEdits: () => void;

  // --- the tree ---
  listings: Record<string, WorkspaceListing>;
  listingErrors: Record<string, string>;
  expanded: string[];
  toggleDirectory: (directory: string) => void;
  /** Ask again for a folder that refused. Clears the sentence first, so the row
   * goes back to "Looking…" and the press is visibly answered either way. */
  retryDirectory: (directory: string) => void;

  // --- the right pane ---
  selection: CodeSelection | null;
  fileView: WorkspaceFileView | null;
  diff: WorkspaceEditDiff | null;
  paneBusy: boolean;
  paneError: string | null;
  openFile: (path: string) => void;
  openEdit: (path: string) => void;

  // --- revert ---
  reverting: string | null;
  revertNotice: string | null;
  revertError: string | null;
  revert: (path: string) => Promise<boolean>;
}

interface Args {
  connected: boolean;
  /** True while the code screen is the visible view. Nothing is fetched otherwise:
   * this screen's data is a read of the person's own disk, and reading it because
   * an app happened to start is a check nobody asked for. */
  active: boolean;
  /** The currently-trusted roots, from `useWorkspace`. Display input only. */
  roots: WorkspaceRoot[];
}

export function useCodeReview({ connected, active, roots }: Args): CodeReviewState {
  const [edits, setEdits] = useState<WorkspaceEdit[]>([]);
  const [editsLoaded, setEditsLoaded] = useState(false);
  const [editsTruncated, setEditsTruncated] = useState(false);
  const [editsError, setEditsError] = useState<string | null>(null);

  const [listings, setListings] = useState<Record<string, WorkspaceListing>>({});
  const [listingErrors, setListingErrors] = useState<Record<string, string>>({});
  const [expanded, setExpanded] = useState<string[]>([]);

  const [selection, setSelection] = useState<CodeSelection | null>(null);
  const [fileView, setFileView] = useState<WorkspaceFileView | null>(null);
  const [diff, setDiff] = useState<WorkspaceEditDiff | null>(null);
  const [paneBusy, setPaneBusy] = useState(false);
  const [paneError, setPaneError] = useState<string | null>(null);

  const [reverting, setReverting] = useState<string | null>(null);
  const [revertNotice, setRevertNotice] = useState<string | null>(null);
  const [revertError, setRevertError] = useState<string | null>(null);

  // The race guard described in the file header.
  const paneSeq = useRef(0);

  const refreshEdits = useCallback(() => {
    if (!isEngineConnected()) return;
    ipc
      .listWorkspaceEdits()
      .then((answer) => {
        if (answer.error !== undefined) {
          // Keep whatever was already listed rather than blanking the column: a
          // stale list with a sentence over it is more use than an empty one.
          setEditsError(answer.error);
          setEditsLoaded(true);
          return;
        }
        setEditsError(null);
        setEdits(answer.value.edits);
        setEditsTruncated(answer.value.truncated);
        setEditsLoaded(true);
      })
      .catch(() => {
        // A REJECTION IS NOT AN EMPTY LIST. `editsLoaded` alone let the column fall
        // through to "Addison hasn't changed any files that are still changed." —
        // stating as fact the one thing a call that never came back cannot know. The
        // resolved-refusal branch above forwards the core's own sentence; this is
        // the same shape for the case where there is no sentence to forward.
        setEditsError(EDITS_UNREADABLE);
        setEditsLoaded(true);
      });
  }, []);

  const clearListingError = useCallback((directory: string) => {
    setListingErrors((prev) => {
      if (!(directory in prev)) return prev;
      const next = { ...prev };
      delete next[directory];
      return next;
    });
  }, []);

  const loadDirectory = useCallback(
    (directory: string) => {
      if (!isEngineConnected()) return;
      ipc
        .listWorkspaceDirectory(directory)
        .then((answer) => {
          if (answer.error !== undefined) {
            setListingErrors((prev) => ({ ...prev, [directory]: answer.error }));
            return;
          }
          clearListingError(directory);
          setListings((prev) => ({ ...prev, [directory]: answer.value }));
        })
        .catch(() => {
          // The folder IS open — `toggleDirectory` expanded it before asking — so a
          // silent catch left "Looking…" on screen for as long as the person stood
          // there, and the next press on the row put the folder away rather than
          // asking again. Say what happened instead, in the same slot the core's own
          // refusal would use, and let the row offer the retry.
          setListingErrors((prev) => ({ ...prev, [directory]: DIRECTORY_UNREADABLE }));
        });
    },
    [clearListingError],
  );

  const retryDirectory = useCallback(
    (directory: string) => {
      // Clear FIRST: with no sentence and no listing the row reads "Looking…"
      // again, which is the only honest thing to show between a press and an
      // answer — and it is how the person can tell the press did something.
      clearListingError(directory);
      loadDirectory(directory);
    },
    [clearListingError, loadDirectory],
  );

  const toggleDirectory = useCallback(
    (directory: string) => {
      if (expanded.includes(directory)) {
        // Closing reads nothing. Putting a folder away is not a question about the
        // disk, and answering it with a listing call would put work behind a click
        // that asked for less, not more.
        setExpanded((prev) => prev.filter((d) => d !== directory));
        return;
      }
      setExpanded((prev) => (prev.includes(directory) ? prev : [...prev, directory]));
      // Re-read on every OPEN rather than trusting the cache: the person is here
      // BECAUSE something changed on disk, and a tree still showing a file Addison
      // deleted ten seconds ago is the failure this screen exists to prevent.
      //
      // The read is deliberately OUTSIDE the state updater: React may invoke an
      // updater more than once (it does under StrictMode), and a fetch inside one
      // would fire twice per click.
      loadDirectory(directory);
    },
    [expanded, loadDirectory],
  );

  const openFile = useCallback((path: string) => {
    if (!isEngineConnected()) return;
    const seq = (paneSeq.current += 1);
    setSelection({ kind: "file", path });
    setDiff(null);
    setFileView(null);
    setPaneError(null);
    setPaneBusy(true);
    ipc
      .readWorkspaceFile(path)
      .then((answer) => {
        if (seq !== paneSeq.current) return; // superseded — see the header
        setPaneBusy(false);
        if (answer.error !== undefined) return setPaneError(answer.error);
        setFileView(answer.value);
      })
      .catch(() => {
        if (seq !== paneSeq.current) return;
        setPaneBusy(false);
      });
  }, []);

  const openEdit = useCallback((path: string) => {
    if (!isEngineConnected()) return;
    const seq = (paneSeq.current += 1);
    setSelection({ kind: "edit", path });
    setFileView(null);
    setDiff(null);
    setPaneError(null);
    setPaneBusy(true);
    setRevertError(null);
    setRevertNotice(null);
    ipc
      .readWorkspaceEditDiff(path)
      .then((answer) => {
        if (seq !== paneSeq.current) return;
        setPaneBusy(false);
        if (answer.error !== undefined) return setPaneError(answer.error);
        setDiff(answer.value);
      })
      .catch(() => {
        if (seq !== paneSeq.current) return;
        setPaneBusy(false);
      });
  }, []);

  /**
   * Put one file back. Resolves to whether it landed, so the caller can close its
   * confirm on success and leave it open on a refusal.
   *
   * A success re-reads BOTH the list and the pane: the chain is now marked
   * reverted, so the row leaves the list, and the diff on screen describes a state
   * that no longer exists. Leaving either behind would offer a second Revert for a
   * change that is already back.
   *
   * A FAILURE ALWAYS SAYS SOMETHING. This is the one control on the screen whose
   * purpose is to change a file, and every path out of it that used to set
   * `revertError` to null left the confirm open with no sentence under it — the
   * person pressed the button and Addison said nothing at all.
   */
  const revert = useCallback(
    async (path: string): Promise<boolean> => {
      if (!isEngineConnected()) {
        setRevertNotice(null);
        setRevertError(REVERT_DISCONNECTED);
        return false;
      }
      setReverting(path);
      setRevertError(null);
      setRevertNotice(null);
      try {
        const result = await ipc.revertWorkspaceFile(path);
        if (!result.ok) {
          // The core's own sentence whenever it sent one — it knows which refusal
          // happened. The fallback is for the resolved-but-wordless answer, which
          // would otherwise render as nothing.
          setRevertError(result.error ?? REVERT_FAILED);
          return false;
        }
        setRevertNotice(result.detail ?? null);
        setSelection(null);
        setDiff(null);
        refreshEdits();
        return true;
      } catch {
        setRevertError(REVERT_FAILED);
        // ...and re-read the list, because a rejected call is the one outcome where
        // this side does not know whether the file was put back. A resolved refusal
        // is the core saying nothing changed; this is nobody saying anything, and
        // the list is what settles it.
        refreshEdits();
        return false;
      } finally {
        setReverting(null);
      }
    },
    [refreshEdits],
  );

  // What the screen is showing right now, held in a ref so the effect below can
  // read it WITHOUT listing it as a dependency: `expanded` changes on every folder
  // click and `selection` on every pick, and an effect that re-fetched the tree
  // each time one did would be a poll with extra steps.
  const onScreen = useRef<{ expanded: string[]; selection: CodeSelection | null }>({
    expanded: [],
    selection: null,
  });
  useEffect(() => {
    onScreen.current = { expanded, selection };
  }, [expanded, selection]);

  // COMING BACK TO THIS SCREEN IS A NEW QUESTION ABOUT THE DISK, and every answer
  // held from last time is a claim about a moment that has passed.
  //
  // The hook lives in App, so nothing here is thrown away when the person leaves.
  // Re-reading only the changes list therefore left the rest of the screen showing
  // the previous visit: a tree still listing a file Addison had since deleted, and
  // a right pane still showing pre-edit text under the correct filename, with no
  // busy state to say either was old. That is exactly the failure `toggleDirectory`
  // re-reads on every open to prevent, one navigation wider.
  //
  // ONCE PER VISIT, NEVER ON A TIMER. The trigger is the false→true edge of
  // `active` — the person opening the screen and nothing else — so standing here
  // re-reads nothing, and neither does a re-render. What it asks for is exactly
  // what is on screen: the changes list, the folders they had open, and the one
  // file or diff in the pane. Never the whole tree, and never a folder nobody
  // opened.
  const wasOnScreen = useRef(false);
  useEffect(() => {
    const live = active && connected;
    const returning = live && !wasOnScreen.current;
    wasOnScreen.current = live;
    if (!returning) return;
    refreshEdits();
    onScreen.current.expanded.forEach(loadDirectory);
    // Re-opened through the ordinary path, so the pane goes busy and empties first:
    // "Opening…" under the right name is honest about not knowing yet, and the old
    // text under the right name is not.
    const open = onScreen.current.selection;
    if (open?.kind === "file") openFile(open.path);
    if (open?.kind === "edit") openEdit(open.path);
  }, [active, connected, refreshEdits, loadDirectory, openFile, openEdit]);

  // Open each trusted root ONE level when the screen first shows them. Never
  // recursive, and `.git` / `node_modules` are listed like everything else and left
  // collapsed — hiding them would be a lie about what is on disk, and expanding
  // them would be a 200,000-entry listing nobody asked for.
  const openedRoots = useRef(false);
  useEffect(() => {
    if (!active || !connected || openedRoots.current) return;
    if (roots.length === 0) return;
    openedRoots.current = true;
    // Merged, not assigned: roots can arrive after the screen is already up, and
    // replacing the set would close a folder the person had just opened.
    setExpanded((prev) => [...new Set([...prev, ...roots.map((r) => r.directory)])]);
    roots.forEach((r) => loadDirectory(r.directory));
  }, [active, connected, roots, loadDirectory]);

  return {
    edits,
    editsLoaded,
    editsTruncated,
    editsError,
    refreshEdits,
    listings,
    listingErrors,
    expanded,
    toggleDirectory,
    retryDirectory,
    selection,
    fileView,
    diff,
    paneBusy,
    paneError,
    openFile,
    openEdit,
    reverting,
    revertNotice,
    revertError,
    revert,
  };
}
