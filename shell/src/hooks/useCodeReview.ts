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
      .catch(() => setEditsLoaded(true));
  }, []);

  const loadDirectory = useCallback((directory: string) => {
    if (!isEngineConnected()) return;
    ipc
      .listWorkspaceDirectory(directory)
      .then((answer) => {
        if (answer.error !== undefined) {
          setListingErrors((prev) => ({ ...prev, [directory]: answer.error }));
          return;
        }
        setListingErrors((prev) => {
          if (!(directory in prev)) return prev;
          const next = { ...prev };
          delete next[directory];
          return next;
        });
        setListings((prev) => ({ ...prev, [directory]: answer.value }));
      })
      .catch(() => {
        /* a rejected call leaves the folder unopened; the row stays clickable */
      });
  }, []);

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
   */
  const revert = useCallback(
    async (path: string): Promise<boolean> => {
      if (!isEngineConnected()) return false;
      setReverting(path);
      setRevertError(null);
      setRevertNotice(null);
      try {
        const result = await ipc.revertWorkspaceFile(path);
        if (!result.ok) {
          setRevertError(result.error ?? null);
          return false;
        }
        setRevertNotice(result.detail ?? null);
        setSelection(null);
        setDiff(null);
        refreshEdits();
        return true;
      } catch {
        setRevertError(null);
        return false;
      } finally {
        setReverting(null);
      }
    },
    [refreshEdits],
  );

  // Read the changes when the screen opens, and again on each visit — this is a
  // question about the disk right now, and the answer goes stale the moment
  // Addison writes another file.
  useEffect(() => {
    if (!active || !connected) return;
    refreshEdits();
  }, [active, connected, refreshEdits]);

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
