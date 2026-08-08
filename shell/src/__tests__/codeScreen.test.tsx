// The review surface — the Developer/Custom screen that shows what Addison changed
// and puts one file back. (Phase-3 review-surface plan Build §4.)
//
// Three halves — the third one added 2026-08-08, because the first two between them
// never ran a line of `useCodeReview`:
//
//   (A) THE GATE, driven through the real App. Whether a Simple-profile window can
//       reach this screen at all is not a rendering detail — trust rows persist
//       across a profile switch, so a person who trusted a folder under Developer
//       and went back to Simple must not be able to browse it from here. The core
//       refuses every call independently; what is asserted here is that this
//       window never even offers the trip.
//
//   (B) THE COPY AND THE CONTROLS, by rendering the surface directly with a hand-
//       built state. Every sentence below is written out byte-for-byte rather than
//       imported from the component, because a test that imports the constant
//       passes when somebody rewrites the sentence — and these are the sentences a
//       person reads immediately before deciding whether to throw work away.
//
//   (C) THE HOOK ITSELF, driven for real through the ipc mock. (B)'s hand-built
//       `CodeReviewState` is a fixture, not the hook, and (A) stubs the whole ipc
//       surface — so every behaviour `useCodeReview`'s own comments call
//       load-bearing (the race guard, the re-read on every open, the merge of
//       late-arriving roots, keeping a stale list rather than blanking it, the
//       re-read after a revert) could be deleted with the suite still green. Each
//       test below names the mutation it kills.
//
// MONACO IS MOCKED, always. It cannot run in jsdom (it needs real layout,
// `ResizeObserver` and `matchMedia`), so the dynamic `import("../lib/monaco")` is
// stubbed and the assertions are about the SURFACE — never about the editor's
// internals, which is the right place for the line anyway. The ONE exception is the
// accessible name, which is not an internal: it is the only thing a screen reader
// gets, it is delivered through `updateOptions`, and the stub records those calls so
// a test can see it.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import {
  render,
  renderHook,
  screen,
  fireEvent,
  cleanup,
  waitFor,
  act,
} from "@testing-library/react";
import { App } from "../App";
import { CodeSurface } from "../components/CodeSurface";
import { Sidebar } from "../components/Sidebar";
import { useCodeReview, type CodeReviewState } from "../hooks/useCodeReview";
import type { WorkspaceEdit, WorkspaceListing } from "../types/protocol";

/** Every option Monaco was configured with, in order. The `ariaLabel` arrives
 * through `updateOptions` rather than at construction, so a stub that swallowed the
 * call made the pane's accessible name unobservable — which is how a session's
 * first pane came to announce Monaco's default "Editor content". */
const monaco = vi.hoisted(() => ({ options: [] as Record<string, unknown>[] }));

/** Whether the engine is up, for the tests that press a button while it is not. */
const engine = vi.hoisted(() => ({ up: true }));

// --- Monaco, stubbed. Nothing below asserts anything about it. ---------------
vi.mock("../lib/monaco", () => {
  const model = { dispose: () => {} };
  const instance = {
    getModel: () => null,
    setModel: () => {},
    updateOptions: (options: Record<string, unknown>) => monaco.options.push(options),
    dispose: () => {},
  };
  return {
    default: {
      editor: {
        create: () => instance,
        createDiffEditor: () => instance,
        createModel: () => model,
        defineTheme: () => {},
        setTheme: () => {},
      },
    },
    languageForPath: () => undefined,
  };
});

// --- The core, stubbed. -----------------------------------------------------
const notifications: Record<string, (params: Record<string, unknown>) => void> = {};
/** Every live "the engine is ready" subscriber. Firing it is how App re-reads the
 * profile — the same path a real engine restart takes. */
const coreState: ((state: string) => void)[] = [];

vi.mock("../ipc/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../ipc/client")>();
  return {
    ...actual,
    isEngineConnected: () => engine.up,
    subscribeCoreState: (handler: (state: string) => void) => {
      coreState.push(handler);
      return () => {};
    },
    subscribeStatus: () => () => {},
    subscribeDiagnostics: () => () => {},
    subscribe: (method: string, handler: (p: Record<string, unknown>) => void) => {
      notifications[method] = handler;
      return () => {};
    },
    ipc: {
      ...actual.ipc,
      getProfile: vi.fn(async () => ({ activeProfile: "simple", profiles: [], flags: {} })),
      listWorkspaceRoots: vi.fn(async () => []),
      listWorkspaceEdits: vi.fn(async () => ({ value: { edits: [], truncated: false } })),
      listWorkspaceDirectory: vi.fn(async () => ({
        value: { directory: "/p", root: "/p", entries: [], truncated: false },
      })),
      readWorkspaceFile: vi.fn(async () => ({
        value: { path: "/p/a.py", root: "/p", content: "x = 1\n", bytes: 6, truncated: false },
      })),
      readWorkspaceEditDiff: vi.fn(async () => ({
        value: { path: "/p/a.py", before: "old\n", after: "new\n", beforeTruncated: false, afterTruncated: false },
      })),
      revertWorkspaceFile: vi.fn(async () => ({ ok: true, path: "/p/a.py", detail: "Put a.py back." })),
    },
  };
});

// Imported AFTER the mock so these are the mocked functions.
import { ipc } from "../ipc/client";

afterEach(cleanup);

/** jsdom ships no matchMedia and every layout decision keys off one. Wide by
 * default: both side columns up, the tree a real column rather than a drawer. */
function stubMatchMedia(matches: (query: string) => boolean = () => false) {
  window.matchMedia = ((query: string) => ({
    matches: matches(query),
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

beforeEach(() => {
  stubMatchMedia();
  localStorage.clear();
  Element.prototype.scrollIntoView = () => {};
  for (const key of Object.keys(notifications)) delete notifications[key];
  coreState.length = 0;
  monaco.options.length = 0;
  engine.up = true;
  vi.mocked(ipc.getProfile).mockResolvedValue({
    activeProfile: "simple",
    profiles: [],
    flags: {},
  });
});

function asProfile(activeProfile: string) {
  vi.mocked(ipc.getProfile).mockResolvedValue({ activeProfile, profiles: [], flags: {} });
}

// ===========================================================================
// (A) The gate
// ===========================================================================

describe("who can reach the code screen at all", () => {
  it("gives the Simple profile no way in — the row is ABSENT, not disabled", async () => {
    // Kills: rendering the nav row unconditionally (or rendering it disabled,
    // which is a control inviting the question "why can't I?"). Trust rows outlive
    // a profile switch core-side, so a Simple window that could browse would be
    // browsing a folder trusted under a profile that is no longer on.
    render(<App />);
    await waitFor(() => expect(ipc.getProfile).toHaveBeenCalled());
    await waitFor(() => expect(screen.getByText("Snapshots")).toBeTruthy());
    expect(screen.queryByText("Code")).toBeNull();
  });

  it("gives Developer and Custom the row, and it opens the screen", async () => {
    // Kills: widening the type and the App state without a nav entry — which
    // leaves a screen with NO WAY TO REACH IT, the dead end the plan names.
    for (const profile of ["developer", "custom"]) {
      asProfile(profile);
      render(<App />);
      const row = await screen.findByText("Code");
      fireEvent.click(row);
      // The header title is the surface's name — that is the screen being open.
      await waitFor(() =>
        expect(screen.getAllByText("Code").length).toBeGreaterThan(1),
      );
      expect(ipc.listWorkspaceEdits).toHaveBeenCalled();
      cleanup();
    }
  });

  it("leaves the screen when the profile stops allowing it", async () => {
    // A profile can change UNDER an open screen — from Settings, or from a G3
    // restore putting a whole configuration back. Kills: gating only the nav entry
    // and leaving the screen rendered for whoever was already standing on it.
    asProfile("developer");
    render(<App />);
    fireEvent.click(await screen.findByText("Code"));
    await waitFor(() => expect(screen.getAllByText("Code").length).toBeGreaterThan(1));

    asProfile("simple");
    // The path a real engine restart (or a G3 restore) takes: App re-reads the
    // profile on every "ready".
    await act(async () => {
      coreState.forEach((h) => h("ready"));
      await Promise.resolve();
    });
    await waitFor(() => expect(screen.queryByText("Code")).toBeNull());
  });

  it("Escape returns to chat from the code screen, like every other surface", async () => {
    // Kills: the Escape handler staying narrowed to `view === "settings"`, which
    // is what it checked before this screen existed.
    asProfile("developer");
    render(<App />);
    fireEvent.click(await screen.findByText("Code"));
    await waitFor(() => expect(screen.getAllByText("Code").length).toBeGreaterThan(1));
    fireEvent.keyDown(window, { key: "Escape" });
    await waitFor(() => expect(screen.getAllByText("Code")).toHaveLength(1));
  });

  it("brings 'Addison's work' with the person, exactly once", async () => {
    // Kills: leaving the Activity Panel in the widget rail, which a surface
    // collapses to zero width and marks `inert` — so the live step list was in the
    // DOM and visible to nobody. And kills the other direction: rendering it on the
    // screen WITHOUT standing the rail's copy down, which is two of them.
    asProfile("developer");
    render(<App />);
    fireEvent.click(await screen.findByText("Code"));
    await act(async () => {
      notifications["tool.activityUpdate"]?.({
        label: "Reading notes.txt…",
        toolId: "read_project_file",
      });
    });
    await waitFor(() => expect(screen.getAllByText("Addison's work")).toHaveLength(1));
  });
});

describe("the sidebar row itself", () => {
  const base = {
    conversations: [],
    currentConversationId: null,
    onOpenConversation: () => {},
    onRenameConversation: () => {},
    onNewChat: () => {},
    newChatDisabled: false,
    onOpenSettings: () => {},
    profileLabel: "Developer profile",
  };

  it("renders the row only when it is given a handler", () => {
    // The presence of the handler IS the gate — App passes one only under
    // Developer/Custom. Kills: a `showCode` flag drifting apart from the handler.
    const { rerender } = render(<Sidebar {...base} view="chat" />);
    expect(screen.queryByText("Code")).toBeNull();
    rerender(<Sidebar {...base} view="chat" onOpenCode={() => {}} />);
    expect(screen.getByText("Code")).toBeTruthy();
  });
});

// ===========================================================================
// (B) The copy and the controls
// ===========================================================================

const EDIT: WorkspaceEdit = {
  path: "/p/src/app.py",
  root: "/p",
  relativePath: "src/app.py",
  snapshotIds: ["s2", "s1"],
  writes: 2,
  created: false,
  firstWrittenAt: 100,
  lastWrittenAt: 200,
  revertable: true,
  onDiskChanged: false,
  missing: false,
};

const LISTING: WorkspaceListing = {
  directory: "/p",
  root: "/p",
  entries: [
    { name: ".git", kind: "directory", size: 96, escapes: false },
    { name: "README.md", kind: "file", size: 812, escapes: false },
    { name: "link", kind: "symlink", size: 11, escapes: true },
  ],
  truncated: true,
};

function reviewState(overrides: Partial<CodeReviewState> = {}): CodeReviewState {
  return {
    edits: [EDIT],
    editsLoaded: true,
    editsTruncated: false,
    editsError: null,
    refreshEdits: () => {},
    listings: { "/p": LISTING },
    listingErrors: {},
    expanded: ["/p"],
    toggleDirectory: () => {},
    retryDirectory: () => {},
    selection: { kind: "edit", path: EDIT.path },
    fileView: null,
    diff: { path: EDIT.path, before: "a\n", after: "b\n", beforeTruncated: false, afterTruncated: false },
    paneBusy: false,
    paneError: null,
    openFile: () => {},
    openEdit: () => {},
    reverting: null,
    revertNotice: null,
    revertError: null,
    revert: async () => true,
    ...overrides,
  };
}

function renderSurface(review: CodeReviewState, extra: Partial<{ turnWorking: boolean }> = {}) {
  return render(
    <CodeSurface
      connected
      roots={[{ directory: "/p" }]}
      rootsLoaded
      review={review}
      theme="dark"
      turnWorking={extra.turnWorking ?? false}
    />,
  );
}

describe("an edit Addison can no longer put back", () => {
  const READ_ONLY_LINE =
    "Addison can't put this file back for you. The earlier version is on the left; " +
    "you can copy it.";

  it("says so and offers NO control", () => {
    // The restart problem, made honest. The shell's ledger of files it has written
    // is session-scoped on purpose and is empty after a restart, while the database
    // rows survive — so this screen would otherwise render a Revert next to every
    // historic edit and every one of them would fail. Kills: the dead-button
    // regression, and kills softening the sentence into "can't be undone".
    renderSurface(reviewState({ edits: [{ ...EDIT, revertable: false }] }));
    expect(screen.getByText(READ_ONLY_LINE)).toBeTruthy();
    expect(screen.queryByText("put it back")).toBeNull();
  });

  it("does not name a cause the boolean cannot carry", () => {
    // `revertable` is ONE boolean on the wire for THREE facts: the file is not in
    // the shell's session write ledger, the shell could not be reached, or the
    // single batch call that answers for every listed edit failed. The sentence used
    // to assert the first for all three, so one failed batch printed "Addison
    // changed this before the app was last restarted" under every row on screen —
    // including files it had changed a minute ago. Kills: putting the cause back
    // before the core can tell the three apart (docs/KNOWN-GAPS.md holds the wire
    // shape that would let it).
    renderSurface(reviewState({ edits: [{ ...EDIT, revertable: false }] }));
    const text = document.body.textContent ?? "";
    expect(text).not.toContain("before the app was last restarted");
    expect(text).toContain("The earlier version is on the left");
  });
});

describe("the warn-before-clobber", () => {
  const CLOBBER =
    "You've changed this file since Addison did. Reverting will replace what's " +
    "there now with the version from before Addison's first change.";
  const CANT_TELL = "Addison can't tell whether this file changed since.";

  it("warns inside the confirm when the file changed since Addison wrote it", async () => {
    // Kills: showing the warning after the press (which is not a warning), and
    // kills dropping it entirely — the case where Revert silently throws away work
    // the person did themselves.
    const revert = vi.fn(async () => true);
    renderSurface(reviewState({ edits: [{ ...EDIT, onDiskChanged: true }], revert }));
    // Not on screen until the person asks: the confirm is the moment it matters.
    expect(screen.queryByText(CLOBBER)).toBeNull();
    fireEvent.click(screen.getByText("put it back"));
    expect(screen.getByText(CLOBBER)).toBeTruthy();
    // And it is a TWO-step confirm — opening it must not have reverted anything.
    expect(revert).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Put it back"));
    await waitFor(() => expect(revert).toHaveBeenCalledTimes(1));
  });

  it("says 'can't tell' rather than nothing when Addison cannot judge", () => {
    // `onDiskChanged` is TRI-STATE and `null` is a real answer — an edit recorded
    // before Addison started hashing what it wrote, or a file the shell cannot
    // read. Kills: collapsing null into false, which is the one wrong reading that
    // reverts somebody's own work with no warning at all.
    renderSurface(reviewState({ edits: [{ ...EDIT, onDiskChanged: null }] }));
    fireEvent.click(screen.getByText("put it back"));
    expect(screen.getByText(CANT_TELL)).toBeTruthy();
    expect(screen.queryByText(CLOBBER)).toBeNull();
  });

  it("says the file will be REMOVED when Addison created it", () => {
    // Kills: the generic "goes back to how it was" sentence on a file that did not
    // exist before — where "putting it back" deletes it.
    renderSurface(reviewState({ edits: [{ ...EDIT, created: true }] }));
    fireEvent.click(screen.getByText("put it back"));
    expect(
      screen.getByText("Addison created app.py. Putting it back removes the file."),
    ).toBeTruthy();
  });
});

describe("revert while Addison is working", () => {
  const BUSY =
    "Addison is working right now. You can put this file back when it has finished.";

  it("holds the control and says why", () => {
    // Kills: dropping the `turn.isWorking` hold. Revert serialises safely behind
    // the worker either way — this is about not asking somebody to reason about
    // two things changing the same file at once.
    renderSurface(reviewState(), { turnWorking: true });
    expect(screen.getByText(BUSY)).toBeTruthy();
    expect(screen.getByText("put it back").closest("button")?.disabled).toBe(true);
  });
});

describe("the file tree", () => {
  const ESCAPES = "This points outside the folder you trusted.";
  const TRUNCATED =
    "This folder holds more than Addison is showing — these are the first entries by name.";

  it("dims a row that leads outside the trusted folder, and clicking it does nothing", () => {
    // The honesty affordance, not the boundary — the boundary is the core refusing
    // the follow-up call. Kills: rendering an escaping entry as an ordinary row,
    // which a person opens before anything refuses.
    //
    // The click is fired for real, on the row and on its container. Asserting only
    // that there is no <button> ancestor pins a PROXY for inertness: an `onClick`
    // added to the row's own div passes that check while making the row live again.
    const openFile = vi.fn();
    const toggleDirectory = vi.fn();
    const { container } = renderSurface(reviewState({ openFile, toggleDirectory }));
    expect(screen.getByText(ESCAPES)).toBeTruthy();
    const row = screen.getByText("link");
    expect(row.closest("button")).toBeNull();
    expect(row.className).toContain("line-through");
    fireEvent.click(row);
    fireEvent.click(container.querySelector("[data-escapes]")!);
    expect(openFile).not.toHaveBeenCalled();
    expect(toggleDirectory).not.toHaveBeenCalled();
  });

  it("has no click handler on the escaping row at all", () => {
    // The other half of the same property, and the half jsdom cannot see: React
    // attaches its listeners at the root, so a handler that does nothing is
    // invisible to any assertion the test above can make. Read at source, the way
    // the CSP injection-site count and the restore closure are. Kills:
    // `onClick={() => undefined}` — a row that answers a press with silence, which
    // is the shape somebody presses twice and then reports as broken.
    const source = readFileSync(join(process.cwd(), "src", "components", "CodeSurface.tsx"), "utf8");
    const block = /if \(entry\.escapes\) \{([\s\S]*?)\n {2}\}/.exec(source);
    expect(block, "the escaping-entry branch has moved — re-point this pin").toBeTruthy();
    expect(block![1]).not.toContain("onClick");
    expect(block![1]).not.toContain("<button");
  });

  it("lists .git rather than hiding it, and leaves it collapsed", () => {
    // Kills: filtering `.git`/`node_modules` out of the tree. Hiding is a lie about
    // what is on disk, and telling the truth about what is on disk is this
    // surface's only value. Collapsed is the UI's job; absent is not.
    renderSurface(reviewState());
    const git = screen.getByText(".git").closest("button");
    expect(git).toBeTruthy();
    expect(git?.getAttribute("aria-expanded")).toBe("false");
  });

  it("says plainly when a folder holds more than it is showing", () => {
    // Kills: dropping `truncated`. A listing that is quietly incomplete is
    // indistinguishable from a file that is not there.
    renderSurface(reviewState());
    expect(screen.getByText(TRUNCATED)).toBeTruthy();
  });

  it("shows a folder's refusal AND a way to ask again", () => {
    // An open folder that could not be listed rendered "Looking…" for as long as
    // the person stood there, and because it was already in `expanded` the next
    // press CLOSED it instead of retrying. Kills: dropping the sentence back into a
    // silent catch, and kills leaving the row with no retry — where the only way
    // back is to close the folder and open it again, which is not a thing anybody
    // works out from a spinner.
    const retryDirectory = vi.fn();
    renderSurface(
      reviewState({
        listings: {},
        listingErrors: { "/p": "Addison couldn't look inside this folder just now." },
        retryDirectory,
      }),
    );
    expect(screen.getByText("Addison couldn't look inside this folder just now.")).toBeTruthy();
    expect(screen.queryByText("Looking…")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Look inside /p again" }));
    expect(retryDirectory).toHaveBeenCalledWith("/p");
  });

  it("calls a socket or an unknown kind 'other', never 'shortcut'", () => {
    // `parseEntryKind` fails closed to `other` for a socket, a device, and for any
    // kind a later core sends that this build has not heard of. Kills: the badge
    // that said "shortcut" for all of them — the row's only statement about what
    // the entry IS, and false for every one of those.
    renderSurface(
      reviewState({
        listings: {
          "/p": {
            directory: "/p",
            root: "/p",
            entries: [{ name: "app.sock", kind: "other", size: 0, escapes: false }],
            truncated: false,
          },
        },
      }),
    );
    expect(screen.getByText("app.sock")).toBeTruthy();
    expect(screen.queryByText("shortcut")).toBeNull();
    expect(screen.getByText("other")).toBeTruthy();
  });
});

describe("a diff that has outlived its edit", () => {
  const STALE =
    "This change isn't in Addison's list any more. What's below is how the file " +
    "looked when you opened it.";

  it("says so, rather than rendering as current with no control", () => {
    // The list is re-read on a revert, on a return to the screen, and whenever the
    // core says something changed. When the open diff's row leaves it, `RevertBlock`
    // disappears — and the pane went on showing the same before/after with nothing
    // to say it was no longer the file's live state. Kills: dropping the sentence
    // and leaving a diff that reads as current.
    renderSurface(reviewState({ edits: [] }));
    expect(screen.getByText(STALE)).toBeTruthy();
    expect(screen.queryByText("put it back")).toBeNull();
  });

  it("says nothing of the sort before the list is an answer", () => {
    // Kills: claiming it off an unloaded or failed list, where EVERY diff would
    // qualify — a sentence about somebody's work having gone, produced by a fetch
    // that has not come back yet.
    renderSurface(reviewState({ edits: [], editsLoaded: false }));
    expect(screen.queryByText(STALE)).toBeNull();
    renderSurface(reviewState({ edits: [], editsError: "Addison couldn't check." }));
    expect(screen.queryByText(STALE)).toBeNull();
  });
});

describe("the pane's accessible name", () => {
  it("names the FIRST pane of a session, not just the second", () => {
    // Monaco arrives lazily. On the render where the chunk lands the label has not
    // changed, so an effect keyed on `[ariaLabel]` alone did not run — and the run
    // before it had called `updateOptions` on a null ref. `BASE_OPTIONS` carries no
    // `ariaLabel`, so the first pane opened in a session announced Monaco's own
    // "Editor content". Kills: taking `mod` back out of that effect's dependencies.
    renderSurface(
      reviewState({
        selection: { kind: "file", path: "/p/src/app.py" },
        diff: null,
        fileView: {
          path: "/p/src/app.py",
          root: "/p",
          content: "x = 1\n",
          bytes: 6,
          truncated: false,
        },
      }),
    );
    return waitFor(() =>
      expect(monaco.options.map((o) => o.ariaLabel)).toContain("app.py, read only"),
    );
  });

  it("names the diff pane too", async () => {
    renderSurface(reviewState());
    await waitFor(() =>
      expect(monaco.options.map((o) => o.ariaLabel)).toContain(
        "app.py, before and after Addison's changes",
      ),
    );
  });
});

describe("'Addison's work' has exactly one home at every width", () => {
  const WORK = <div>the live step list</div>;

  function renderAt(narrow: boolean) {
    stubMatchMedia((q) => narrow && /max-width:\s*767\.98px/.test(q));
    return render(
      <CodeSurface
        connected
        roots={[{ directory: "/p" }]}
        rootsLoaded
        review={reviewState()}
        theme="dark"
        turnWorking={false}
        work={WORK}
      />,
    );
  }

  it("puts it in the tree column when there is one", () => {
    renderAt(false);
    expect(screen.getAllByText("the live step list")).toHaveLength(1);
  });

  it("moves it beside the pane when the tree is a closed drawer", () => {
    // Kills: leaving it in the left column on a narrow window, where that column is
    // a drawer that starts CLOSED — the same "in the DOM, visible to nobody"
    // failure as leaving it in the collapsed rail, one layout later.
    const { container } = renderAt(true);
    expect(screen.getAllByText("the live step list")).toHaveLength(1);
    // And it is outside the drawer, which is not even mounted while closed.
    expect(container.querySelector('[role="dialog"]')).toBeNull();
  });
});

describe("the empty and disconnected states", () => {
  it("says there is nothing to review rather than showing an empty column", () => {
    renderSurface(reviewState({ edits: [], selection: null, diff: null }));
    expect(
      screen.getByText("Addison hasn't changed any files that are still changed."),
    ).toBeTruthy();
    expect(screen.getByText("Pick a change or a file to see it here.")).toBeTruthy();
  });

  it("points at Settings when no folder is trusted yet", () => {
    render(
      <CodeSurface
        connected
        roots={[]}
        rootsLoaded
        review={reviewState({ edits: [], selection: null, diff: null })}
        theme="dark"
        turnWorking={false}
      />,
    );
    expect(
      screen.getByText(
        'Addison isn\'t working in any folders yet. Choose one in Settings, under "Folders Addison may work in".',
      ),
    ).toBeTruthy();
  });

  it("does not leave a permanent 'Looking…' under the folders label with no engine", () => {
    // The folders column was gated on `rootsLoaded` alone, so an engine that is down
    // rendered "isn't connected" over one column and a spinner under the other —
    // waiting for an answer nobody is coming back with. Kills: gating that column on
    // anything but `connected` first.
    render(
      <CodeSurface
        connected={false}
        roots={[]}
        rootsLoaded={false}
        review={reviewState({ edits: [], editsLoaded: false, selection: null, diff: null })}
        theme="dark"
        turnWorking={false}
      />,
    );
    expect(
      screen.getByText(
        "Addison's engine isn't connected, so it can't say which folders it may work in.",
      ),
    ).toBeTruthy();
    expect(screen.queryByText("Looking…")).toBeNull();
  });
});

// ===========================================================================
// (C) The hook, driven for real
// ===========================================================================
// `useCodeReview` had no behavioural coverage at all: (B) hands the surface a
// hand-built state object and (A) stubs ipc wholesale, so every rule the hook's own
// comments call load-bearing could be deleted with the suite still green. These
// drive the real hook through the same ipc mock, one test per rule, each naming
// what it kills.

/** The hook's own four sentences, byte-for-byte — the ones it writes when the core
 * never answered and there is nothing to forward. Written out rather than imported,
 * like every other frozen string in this file. */
const EDITS_UNREADABLE =
  "Addison couldn't check which files it has changed just now. Leaving this screen " +
  "and coming back will make Addison check again.";
const DIRECTORY_UNREADABLE = "Addison couldn't look inside this folder just now.";
const REVERT_FAILED = "Addison couldn't put this file back just now. You can try again.";
const REVERT_DISCONNECTED =
  "Addison's engine isn't connected, so it can't put this file back. Try again in a moment.";

/** The mocks back to a working default with their call counts cleared. Each test
 * varies the one call it is about. */
function resetIpc() {
  vi.clearAllMocks();
  vi.mocked(ipc.listWorkspaceEdits).mockResolvedValue({
    value: { edits: [], truncated: false },
  });
  vi.mocked(ipc.listWorkspaceDirectory).mockImplementation(async (directory: string) => ({
    value: { directory, root: "/p", entries: [], truncated: false },
  }));
  // Echoes the path back, so a test can tell WHICH answer landed in the pane.
  vi.mocked(ipc.readWorkspaceFile).mockImplementation(async (path: string) => ({
    value: { path, root: "/p", content: `# ${path}\n`, bytes: 8, truncated: false },
  }));
  vi.mocked(ipc.readWorkspaceEditDiff).mockImplementation(async (path: string) => ({
    value: { path, before: "a\n", after: "b\n", beforeTruncated: false, afterTruncated: false },
  }));
  vi.mocked(ipc.revertWorkspaceFile).mockResolvedValue({
    ok: true,
    path: "/p/src/app.py",
    detail: "Put app.py back.",
  });
}

interface HookProps {
  active: boolean;
  connected: boolean;
  roots: { directory: string }[];
}

function mountHook(props: Partial<HookProps> = {}) {
  return renderHook((p: HookProps) => useCodeReview(p), {
    initialProps: {
      active: props.active ?? true,
      connected: props.connected ?? true,
      roots: props.roots ?? [],
    },
  });
}

describe("the changes list", () => {
  beforeEach(resetIpc);

  it("says it could not find out, rather than 'nothing has changed'", async () => {
    // A rejected call set `editsLoaded` and nothing else, so the column fell through
    // to "Addison hasn't changed any files that are still changed." — stating as
    // fact the one thing a call that never came back cannot know, on the screen
    // somebody opens BECAUSE they think Addison changed something. Kills: a catch
    // that only flips the loaded flag.
    vi.mocked(ipc.listWorkspaceEdits).mockRejectedValue(new Error("engine went away"));
    const { result } = mountHook();
    await waitFor(() => expect(result.current.editsLoaded).toBe(true));
    expect(result.current.editsError).toBe(EDITS_UNREADABLE);
  });

  it("keeps the last list it had when a refresh is refused", async () => {
    // Kills: blanking `edits` in the error branch. A stale list with a sentence over
    // it is more use than an empty one — and an empty one is also a claim.
    vi.mocked(ipc.listWorkspaceEdits).mockResolvedValueOnce({
      value: { edits: [EDIT], truncated: false },
    });
    const { result } = mountHook();
    await waitFor(() => expect(result.current.edits).toHaveLength(1));
    vi.mocked(ipc.listWorkspaceEdits).mockResolvedValueOnce({
      error: "Addison can't look there.",
    });
    act(() => result.current.refreshEdits());
    await waitFor(() => expect(result.current.editsError).toBe("Addison can't look there."));
    expect(result.current.edits).toHaveLength(1);
  });
});

describe("the tree, from the hook's side", () => {
  beforeEach(resetIpc);

  it("re-reads a folder on EVERY open, and reads nothing at all on a close", async () => {
    // The person is here BECAUSE something changed on disk; a tree still showing a
    // file Addison deleted ten seconds ago is the failure this screen exists to
    // prevent. And closing is not a question about the disk. Kills: serving an open
    // from the cache, and kills fetching on a collapse.
    const { result } = mountHook();
    act(() => result.current.toggleDirectory("/p/src"));
    await waitFor(() => expect(result.current.listings["/p/src"]).toBeTruthy());
    act(() => result.current.toggleDirectory("/p/src")); // closed
    expect(vi.mocked(ipc.listWorkspaceDirectory).mock.calls).toHaveLength(1);
    act(() => result.current.toggleDirectory("/p/src")); // ...and open again
    await waitFor(() => expect(vi.mocked(ipc.listWorkspaceDirectory).mock.calls).toHaveLength(2));
  });

  it("merges roots that arrive late instead of replacing what is open", async () => {
    // Roots can arrive after the screen is already up. Kills: assigning the root set
    // over `expanded`, which closes a folder the person had just opened.
    const { result, rerender } = mountHook({ roots: [] });
    act(() => result.current.toggleDirectory("/p/src"));
    await waitFor(() => expect(result.current.expanded).toContain("/p/src"));
    rerender({ active: true, connected: true, roots: [{ directory: "/p" }] });
    await waitFor(() => expect(result.current.expanded).toContain("/p"));
    expect(result.current.expanded).toContain("/p/src");
  });

  it("says why a folder is empty, and asks again when pressed", async () => {
    // A rejected listing left the folder open showing "Looking…" forever — and the
    // folder being in `expanded` meant the next press put it away rather than asking
    // again. Kills: the silent catch, and kills a retry that does not clear the
    // sentence first (the row would answer a press with the same refusal still on
    // screen, which is indistinguishable from nothing happening).
    vi.mocked(ipc.listWorkspaceDirectory).mockRejectedValueOnce(new Error("engine went away"));
    const { result } = mountHook();
    act(() => result.current.toggleDirectory("/p/src"));
    await waitFor(() => expect(result.current.listingErrors["/p/src"]).toBe(DIRECTORY_UNREADABLE));
    expect(result.current.expanded).toContain("/p/src");

    // THE SENTENCE GOES WHILE THE ANSWER IS STILL IN FLIGHT, which is the only
    // moment the clearing is observable: on success the load clears it anyway, so
    // asserting after the answer lands passes whether or not the retry cleared
    // anything — this docstring named that mutation and did not kill it (found by
    // re-running the claimed mutations, 2026-08-08). Hold the answer back and look
    // in between.
    type Listing = Awaited<ReturnType<typeof ipc.listWorkspaceDirectory>>;
    let land: (answer: Listing) => void = () => {};
    vi.mocked(ipc.listWorkspaceDirectory).mockImplementationOnce(
      () =>
        new Promise<Listing>((resolve) => {
          land = resolve;
        }),
    );
    act(() => result.current.retryDirectory("/p/src"));
    expect(result.current.listingErrors["/p/src"]).toBeUndefined();
    expect(result.current.listings["/p/src"]).toBeUndefined();

    await act(async () => {
      land({
        error: undefined,
        value: { directory: "/p/src", root: "/p", entries: [], truncated: false },
      });
    });
    await waitFor(() => expect(result.current.listings["/p/src"]).toBeTruthy());
    expect(result.current.listingErrors["/p/src"]).toBeUndefined();
  });

  it("keeps the sentence when the retry cannot even ask", async () => {
    // The other half of the ordering, and the reason the clearing moved AFTER the
    // call: `loadDirectory` declines silently with no engine, so clearing first
    // would strip the row of its sentence AND its retry button and leave a
    // permanent "Looking…" — the exact state the retry was added to abolish.
    // Kills: clearing before the call, or ignoring what `loadDirectory` answers.
    vi.mocked(ipc.listWorkspaceDirectory).mockRejectedValueOnce(new Error("engine went away"));
    const { result } = mountHook();
    act(() => result.current.toggleDirectory("/p/src"));
    await waitFor(() => expect(result.current.listingErrors["/p/src"]).toBe(DIRECTORY_UNREADABLE));

    engine.up = false;
    act(() => result.current.retryDirectory("/p/src"));
    expect(result.current.listingErrors["/p/src"]).toBe(DIRECTORY_UNREADABLE);
    expect(result.current.listings["/p/src"]).toBeUndefined();
  });

  it("clears the refusal when the folder is simply opened again", async () => {
    // The retry is not the only way back to a folder that refused: close it and open
    // it again and you are asking the same question by the longer route. That press
    // re-read for real, but `toggleDirectory` never cleared the sentence, so the row
    // answered with the OLD refusal while the fresh listing was already in flight —
    // stating as fact a refusal nobody had repeated, which is the exact thing
    // `retryDirectory` argues against three lines above it. Kills: dropping the
    // clear from the open path. The test below holds the other half of the ordering.
    vi.mocked(ipc.listWorkspaceDirectory).mockRejectedValueOnce(new Error("engine went away"));
    const { result } = mountHook();
    act(() => result.current.toggleDirectory("/p/src"));
    await waitFor(() => expect(result.current.listingErrors["/p/src"]).toBe(DIRECTORY_UNREADABLE));
    act(() => result.current.toggleDirectory("/p/src")); // put away
    expect(result.current.expanded).not.toContain("/p/src");

    // Held back, because the moment the clearing is observable is the one BETWEEN
    // the press and the answer — on success the load clears it anyway.
    type Listing = Awaited<ReturnType<typeof ipc.listWorkspaceDirectory>>;
    let land: (answer: Listing) => void = () => {};
    vi.mocked(ipc.listWorkspaceDirectory).mockImplementationOnce(
      () =>
        new Promise<Listing>((resolve) => {
          land = resolve;
        }),
    );
    act(() => result.current.toggleDirectory("/p/src")); // ...and open again
    expect(result.current.listingErrors["/p/src"]).toBeUndefined();
    expect(result.current.listings["/p/src"]).toBeUndefined();

    await act(async () => {
      land({ value: { directory: "/p/src", root: "/p", entries: [], truncated: false } });
    });
    await waitFor(() => expect(result.current.listings["/p/src"]).toBeTruthy());
  });

  it("keeps the refusal when reopening cannot even ask", async () => {
    // The same ordering `retryDirectory` has, and the reason the clear goes AFTER the
    // call rather than before it: with no engine `loadDirectory` declines silently, so
    // clearing anyway would strip the row of its sentence AND its retry and leave a
    // permanent "Looking…" — the state both of these exist to abolish. Kills: clearing
    // on the way in, before anything has been asked.
    vi.mocked(ipc.listWorkspaceDirectory).mockRejectedValueOnce(new Error("engine went away"));
    const { result } = mountHook();
    act(() => result.current.toggleDirectory("/p/src"));
    await waitFor(() => expect(result.current.listingErrors["/p/src"]).toBe(DIRECTORY_UNREADABLE));
    act(() => result.current.toggleDirectory("/p/src")); // put away

    engine.up = false;
    act(() => result.current.toggleDirectory("/p/src")); // ...and open again
    expect(result.current.listingErrors["/p/src"]).toBe(DIRECTORY_UNREADABLE);
    expect(result.current.listings["/p/src"]).toBeUndefined();
  });
});

describe("the right pane's race guard", () => {
  beforeEach(resetIpc);

  it("drops an answer for a file the person has already navigated away from", async () => {
    // Without it, clicking two files quickly leaves whichever answer arrived last on
    // screen under whichever header arrived first — one file's text under another
    // file's name, on the one screen whose entire job is to be exact about which
    // file is which. Kills: deleting the `seq !== paneSeq.current` check.
    type FileAnswer = Awaited<ReturnType<typeof ipc.readWorkspaceFile>>;
    let landFirst: (answer: FileAnswer) => void = () => {};
    vi.mocked(ipc.readWorkspaceFile).mockImplementationOnce(
      () =>
        new Promise<FileAnswer>((resolve) => {
          landFirst = resolve;
        }),
    );
    const { result } = mountHook();
    act(() => result.current.openFile("/p/first.py"));
    act(() => result.current.openFile("/p/second.py"));
    await waitFor(() => expect(result.current.fileView?.path).toBe("/p/second.py"));
    await act(async () => {
      landFirst({
        value: { path: "/p/first.py", root: "/p", content: "late\n", bytes: 5, truncated: false },
      });
      await Promise.resolve();
    });
    expect(result.current.fileView?.path).toBe("/p/second.py");
    expect(result.current.selection?.path).toBe("/p/second.py");
  });
});

describe("putting a file back, from the hook's side", () => {
  beforeEach(resetIpc);

  it("re-reads the changes list on success, so the row cannot be reverted twice", async () => {
    // The chain is marked reverted core-side, so the row leaves the list and the
    // diff on screen describes a state that no longer exists. Kills: dropping that
    // refresh, which leaves a second Revert on screen for a change already back.
    const { result } = mountHook();
    await waitFor(() => expect(ipc.listWorkspaceEdits).toHaveBeenCalledTimes(1));
    act(() => result.current.openEdit("/p/src/app.py"));
    await waitFor(() => expect(result.current.diff).toBeTruthy());
    await act(async () => {
      await result.current.revert("/p/src/app.py");
    });
    expect(ipc.listWorkspaceEdits).toHaveBeenCalledTimes(2);
    expect(result.current.selection).toBeNull();
    expect(result.current.diff).toBeNull();
    expect(result.current.revertNotice).toBe("Put app.py back.");
  });

  it("says something when the call does not come back", async () => {
    // The ONE control on this screen whose purpose is to change a file. It set
    // `revertError` to null, so the confirm stayed open with no sentence under it:
    // the person pressed the button and Addison said nothing at all. Kills: a catch
    // that swallows. The list is re-read too, because a rejected call is the one
    // outcome where this side does not know whether the file was put back.
    vi.mocked(ipc.revertWorkspaceFile).mockRejectedValueOnce(new Error("engine went away"));
    const { result } = mountHook();
    await waitFor(() => expect(ipc.listWorkspaceEdits).toHaveBeenCalledTimes(1));
    let landed = true;
    await act(async () => {
      landed = await result.current.revert("/p/src/app.py");
    });
    expect(landed).toBe(false);
    expect(result.current.revertError).toBe(REVERT_FAILED);
    expect(ipc.listWorkspaceEdits).toHaveBeenCalledTimes(2);
  });

  it("prefers the core's own refusal, and falls back when it sent none", async () => {
    // Kills: `result.error ?? null`, which renders a wordless {ok:false} as silence.
    vi.mocked(ipc.revertWorkspaceFile).mockResolvedValueOnce({
      ok: false,
      path: "/p/src/app.py",
      error: "That file has moved since Addison changed it.",
    });
    const { result } = mountHook();
    await act(async () => {
      await result.current.revert("/p/src/app.py");
    });
    expect(result.current.revertError).toBe("That file has moved since Addison changed it.");
    vi.mocked(ipc.revertWorkspaceFile).mockResolvedValueOnce({ ok: false, path: "/p/src/app.py" });
    await act(async () => {
      await result.current.revert("/p/src/app.py");
    });
    expect(result.current.revertError).toBe(REVERT_FAILED);
  });

  it("says the engine is down rather than doing nothing at all", async () => {
    // The early return sent back `false` and left every sentence null, which on this
    // button is indistinguishable from a press that did not register. Kills: the
    // bare `return false`.
    const { result } = mountHook();
    engine.up = false;
    await act(async () => {
      await result.current.revert("/p/src/app.py");
    });
    expect(result.current.revertError).toBe(REVERT_DISCONNECTED);
    expect(ipc.revertWorkspaceFile).not.toHaveBeenCalled();
  });
});

describe("coming back to the screen", () => {
  beforeEach(resetIpc);

  it("re-reads the changes, the open folders AND the open pane", async () => {
    // The hook lives in App, so nothing is thrown away when the person leaves. Only
    // the changes list was re-read on a return, so the tree went on listing a file
    // Addison had since deleted and the pane went on showing pre-edit text under the
    // correct filename, with no busy state to say either was old. Kills: re-reading
    // the list alone.
    const roots = [{ directory: "/p" }];
    const { result, rerender } = mountHook({ roots });
    await waitFor(() => expect(result.current.expanded).toContain("/p"));
    act(() => result.current.openFile("/p/src/app.py"));
    await waitFor(() => expect(result.current.fileView?.path).toBe("/p/src/app.py"));
    const before = {
      edits: vi.mocked(ipc.listWorkspaceEdits).mock.calls.length,
      dirs: vi.mocked(ipc.listWorkspaceDirectory).mock.calls.length,
      files: vi.mocked(ipc.readWorkspaceFile).mock.calls.length,
    };

    rerender({ active: false, connected: true, roots });
    rerender({ active: true, connected: true, roots });

    expect(vi.mocked(ipc.listWorkspaceEdits).mock.calls.length).toBe(before.edits + 1);
    expect(vi.mocked(ipc.listWorkspaceDirectory).mock.calls.length).toBe(before.dirs + 1);
    expect(vi.mocked(ipc.listWorkspaceDirectory).mock.calls[before.dirs][0]).toBe("/p");
    expect(vi.mocked(ipc.readWorkspaceFile).mock.calls.length).toBe(before.files + 1);
    // ...and the pane says so while it waits, rather than showing the old text.
    expect(result.current.paneBusy).toBe(true);
    await waitFor(() => expect(result.current.paneBusy).toBe(false));
  });

  it("re-reads nothing while the person simply stands there, or WORKS here", async () => {
    // Kills: turning the visit re-read into a poll — an effect keyed on `expanded`
    // or `selection` re-fetches the whole tree on every click, which is a poll with
    // extra steps.
    //
    // A RE-RENDER IS NOT ENOUGH TO SHOW THAT, which is why this test does the work
    // as well: with the deps as they are, a plain re-render re-runs nothing whether
    // the edge guard is there or not, so the two halves of that mutation each
    // survived the re-render-only version of this test and only failed together
    // (found re-running the claimed mutations, 2026-08-08). Opening a folder and
    // picking a file are what MOVE `expanded` and `selection`; each must ask for the
    // one thing it is about and nothing else. Either half of the mutation on its own
    // is unobservable from out here and is pinned at source instead — see below.
    const roots = [{ directory: "/p" }];
    const { result, rerender } = mountHook({ roots });
    await waitFor(() => expect(result.current.expanded).toContain("/p"));
    const before = vi.mocked(ipc.listWorkspaceDirectory).mock.calls.length;
    rerender({ active: true, connected: true, roots });
    rerender({ active: true, connected: true, roots });
    expect(vi.mocked(ipc.listWorkspaceDirectory).mock.calls.length).toBe(before);
    expect(ipc.listWorkspaceEdits).toHaveBeenCalledTimes(1);

    // Opening a folder asks for THAT folder — never for `/p` again, and never for
    // the changes list.
    act(() => result.current.toggleDirectory("/p/src"));
    await waitFor(() => expect(result.current.listings["/p/src"]).toBeTruthy());
    expect(vi.mocked(ipc.listWorkspaceDirectory).mock.calls.length).toBe(before + 1);
    expect(ipc.listWorkspaceEdits).toHaveBeenCalledTimes(1);

    // ...and picking a file asks for that file, and touches neither the tree nor the
    // list.
    act(() => result.current.openFile("/p/a.py"));
    await waitFor(() => expect(result.current.fileView?.path).toBe("/p/a.py"));
    expect(vi.mocked(ipc.listWorkspaceDirectory).mock.calls.length).toBe(before + 1);
    expect(ipc.listWorkspaceEdits).toHaveBeenCalledTimes(1);
  });

  it("cannot become a poll — and neither half of that mutation survives", () => {
    // The source-level half, read the way the CSP injection-site count and the
    // escaping-row branch are read. It is here because the two halves of the poll
    // mutation are each INVISIBLE from behaviour: keying the effect on `expanded`
    // while the edge guard still returns early fetches nothing, and dropping the
    // guard while the deps are still `[active, connected, …stable callbacks]` re-runs
    // on exactly the transitions it ran on before. Only the pair is observable, so
    // only the pair is killable behaviourally — and a mutation nothing catches is a
    // rule nothing holds.
    //
    // Kills, separately: adding `expanded` or `selection` to the dependency array,
    // and deleting the false→true edge guard that makes this once-per-visit.
    const source = readFileSync(join(process.cwd(), "src", "hooks", "useCodeReview.ts"), "utf8");
    const effect =
      /const wasOnScreen = useRef\(false\);\s*useEffect\(\(\) => \{([\s\S]*?)\n {2}\}, \[([^\]]*)\]\);/.exec(
        source,
      );
    expect(effect, "the come-back effect has moved — re-point this pin").toBeTruthy();
    const [, body, deps] = effect!;
    // The edge, both halves of it: the read that makes it an edge and the write that
    // arms it for next time.
    expect(body).toContain("!wasOnScreen.current");
    expect(body).toMatch(/wasOnScreen\.current = /);
    expect(body).toMatch(/return;/);
    // ...and the deps that keep it a visit rather than a subscription to the screen.
    expect(deps).not.toMatch(/\bexpanded\b/);
    expect(deps).not.toMatch(/\bselection\b/);
  });

  it("reads nothing at all while the screen is not the one on show", async () => {
    // This is a read of the person's own disk. Kills: dropping the `active` guard —
    // a check nobody asked for, run because an app happened to start.
    mountHook({ active: false, roots: [{ directory: "/p" }] });
    await new Promise((r) => setTimeout(r, 10));
    expect(ipc.listWorkspaceEdits).not.toHaveBeenCalled();
    expect(ipc.listWorkspaceDirectory).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// The OTHER race, and the one the come-back re-read created. `paneSeq` guards which
// PICK the pane is showing; nothing guarded which VISIT asked, so the list and the
// listings gained two more concurrent writers with no discipline at all. Leaving the
// screen cancels nothing, so every answer visit N is still waiting on lands during
// visit N+1 and overwrites what visit N+1 just found out. All three shapes below
// were reproduced before the counter existed.
// ---------------------------------------------------------------------------

describe("an answer from the visit before", () => {
  beforeEach(resetIpc);

  const roots = [{ directory: "/p" }];
  type Listing = Awaited<ReturnType<typeof ipc.listWorkspaceDirectory>>;
  type Edits = Awaited<ReturnType<typeof ipc.listWorkspaceEdits>>;

  /** Off the screen and back on: the false→true edge of `active`, which is the one
   * thing that starts a new visit. */
  function leaveAndReturn(rerender: (props: HookProps) => void) {
    rerender({ active: false, connected: true, roots });
    rerender({ active: true, connected: true, roots });
  }

  it("does not walk a deleted file back into the tree", async () => {
    // Visit 1 asked what is in `/p` and never got an answer. Visit 2 asked again and
    // was told the file is gone — which is WHY the person is on this screen. Then
    // visit 1's answer arrives and puts the file back in the tree, with a row that
    // opens it. Kills: applying a listing without checking which visit asked for it.
    let landVisitOne: (answer: Listing) => void = () => {};
    vi.mocked(ipc.listWorkspaceDirectory).mockImplementationOnce(
      () =>
        new Promise<Listing>((resolve) => {
          landVisitOne = resolve;
        }),
    );
    const { result, rerender } = mountHook({ roots });
    await waitFor(() => expect(result.current.expanded).toContain("/p"));

    leaveAndReturn(rerender);
    await waitFor(() => expect(result.current.listings["/p"]).toBeTruthy());
    expect(result.current.listings["/p"].entries).toHaveLength(0);

    await act(async () => {
      landVisitOne({
        value: {
          directory: "/p",
          root: "/p",
          entries: [{ name: "gone.py", kind: "file", size: 12, escapes: false }],
          truncated: false,
        },
      });
    });
    expect(result.current.listings["/p"].entries).toHaveLength(0);
  });

  it("does not put last visit's refusal over a listing that arrived and is good", async () => {
    // The nastier direction, because `DirectoryNode` renders an error BEFORE it
    // renders entries: a rejection left over from a visit the person has already left
    // hides a listing that is on screen, correct and current, behind "Addison couldn't
    // look inside this folder just now." and a "try again" for a question that has
    // already been answered. Kills: a catch that files a sentence without checking
    // which visit was asking.
    let failVisitOne: (reason: Error) => void = () => {};
    vi.mocked(ipc.listWorkspaceDirectory).mockImplementationOnce(
      () =>
        new Promise<Listing>((_resolve, reject) => {
          failVisitOne = reject;
        }),
    );
    const { result, rerender } = mountHook({ roots });
    await waitFor(() => expect(result.current.expanded).toContain("/p"));

    leaveAndReturn(rerender);
    await waitFor(() => expect(result.current.listings["/p"]).toBeTruthy());

    await act(async () => {
      failVisitOne(new Error("engine went away"));
    });
    expect(result.current.listingErrors["/p"]).toBeUndefined();
    expect(result.current.listings["/p"]).toBeTruthy();
  });

  it("does not bring a reverted row back with a live 'put it back' beside it", async () => {
    // The changes list is the third writer. Visit 1's list is still in flight when the
    // person leaves; they come back to a list that no longer holds the row they
    // reverted, and then visit 1's answer restores it — a Revert control for a change
    // that is already back, on the one screen whose job is to be exact about what
    // Addison has done. Kills: applying a changes list without checking the visit.
    let landVisitOne: (answer: Edits) => void = () => {};
    vi.mocked(ipc.listWorkspaceEdits).mockImplementationOnce(
      () =>
        new Promise<Edits>((resolve) => {
          landVisitOne = resolve;
        }),
    );
    const { result, rerender } = mountHook({ roots });
    await waitFor(() => expect(ipc.listWorkspaceEdits).toHaveBeenCalledTimes(1));

    leaveAndReturn(rerender);
    await waitFor(() => expect(result.current.editsLoaded).toBe(true));
    expect(result.current.edits).toHaveLength(0);

    await act(async () => {
      landVisitOne({ value: { edits: [EDIT], truncated: false } });
    });
    expect(result.current.edits).toHaveLength(0);
  });

  it("keeps the answers the CURRENT visit is waiting on", async () => {
    // The other direction, and the one an over-eager guard breaks: a counter bumped
    // on anything other than arriving at the screen — a pick, a folder, a re-render —
    // would drop the visit's own answers and leave the screen on "Looking…" forever.
    // Kills: bumping the visit number anywhere but the false→true edge.
    let landCurrent: (answer: Listing) => void = () => {};
    const { result, rerender } = mountHook({ roots });
    await waitFor(() => expect(result.current.listings["/p"]).toBeTruthy());

    vi.mocked(ipc.listWorkspaceDirectory).mockImplementationOnce(
      () =>
        new Promise<Listing>((resolve) => {
          landCurrent = resolve;
        }),
    );
    act(() => result.current.toggleDirectory("/p/src"));
    act(() => result.current.openFile("/p/a.py"));
    rerender({ active: true, connected: true, roots });
    await act(async () => {
      landCurrent({
        value: {
          directory: "/p/src",
          root: "/p",
          entries: [{ name: "app.py", kind: "file", size: 12, escapes: false }],
          truncated: false,
        },
      });
    });
    expect(result.current.listings["/p/src"]?.entries).toHaveLength(1);
  });
});

// ===========================================================================
// (D) The hook and the surface together
// ===========================================================================
// (B) renders the surface over a hand-built state and (C) reads the hook's state
// without rendering anything. Neither can see the failure that needs both: a
// sentence written into state that nothing on screen is left to render.
//
// That is exactly what happened to the one control on this screen whose purpose is
// to change a file. A failed Revert sets `revertError` AND re-reads the changes
// list, and the sentence used to render at a single site two conditionals deep
// inside `RevertBlock` — which that re-read can take away underneath it. (C)'s test
// asserted the hook state and passed while the person saw nothing.

const LIVE_ROOTS = [{ directory: "/p" }];

/** The real hook, driven through the real surface and the same ipc mock. */
function LiveCodeScreen() {
  const review = useCodeReview({ connected: true, active: true, roots: LIVE_ROOTS });
  return (
    <CodeSurface
      connected
      roots={LIVE_ROOTS}
      rootsLoaded
      review={review}
      theme="dark"
      turnWorking={false}
    />
  );
}

describe("a Revert that failed says so ON SCREEN", () => {
  beforeEach(resetIpc);

  const NOT_REVERTABLE =
    "Addison can't put this file back for you. The earlier version is on the left; " +
    "you can copy it.";
  const STALE =
    "This change isn't in Addison's list any more. What's below is how the file " +
    "looked when you opened it.";

  /** Open the one changed file and press through the two-step confirm. */
  async function pressPutItBack() {
    render(<LiveCodeScreen />);
    fireEvent.click(await screen.findByText("src/app.py"));
    fireEvent.click(await screen.findByText("put it back"));
    fireEvent.click(screen.getByText("Put it back"));
  }

  // EVERY ASSERTION BELOW WAITS FOR THE RE-READ TO LAND FIRST, and that ordering is
  // the whole test. A sentence rendered inside `RevertBlock` IS on screen for the
  // microtask between `setRevertError` and the list answer coming back, so a
  // `findByText` fired straight after the press passes against the broken code and
  // proves nothing (this test did, until 2026-08-08). What the person gets to READ
  // is whatever is there once everything has settled.

  it("when the failure's own re-read takes the row out of the list", async () => {
    // Reproduced 2026-08-08: the catch re-reads the changes list (a rejected call is
    // the one outcome where this side does not know whether the file was put back),
    // the row is gone from the answer, `RevertBlock` unmounts — and it was holding
    // the only render of `revertError`. The person pressed a button that failed and
    // was told "This change isn't in Addison's list any more", which is a different
    // fact and reads like success. Kills: putting the sentence back inside
    // `RevertBlock`, where a row that leaves the list deletes it.
    vi.mocked(ipc.listWorkspaceEdits)
      .mockResolvedValueOnce({ value: { edits: [EDIT], truncated: false } })
      .mockResolvedValue({ value: { edits: [], truncated: false } });
    vi.mocked(ipc.revertWorkspaceFile).mockRejectedValueOnce(new Error("engine went away"));

    await pressPutItBack();

    // The row really did go — this is the shape, not a version of it where the block
    // happened to survive.
    await waitFor(() => expect(screen.queryByText("put it back")).toBeNull());
    // The stale-diff sentence is what used to stand in the failure's place. Both are
    // true; the failure is no longer the one missing.
    expect(screen.getByText(STALE)).toBeTruthy();
    expect(screen.getByText(REVERT_FAILED)).toBeTruthy();
  });

  it("when the row comes back with no Revert on it", async () => {
    // The second shape, and it does not need the row to leave at all: the re-read
    // answers with `revertable: false` (the shell's write ledger is session-scoped,
    // and a core that has just restarted says exactly this), `RevertBlock` takes its
    // early return, and the person gets the read-only line instead of the failure.
    // Kills: rendering the sentence after any early return in that block.
    vi.mocked(ipc.listWorkspaceEdits)
      .mockResolvedValueOnce({ value: { edits: [EDIT], truncated: false } })
      .mockResolvedValue({
        value: { edits: [{ ...EDIT, revertable: false }], truncated: false },
      });
    vi.mocked(ipc.revertWorkspaceFile).mockRejectedValueOnce(new Error("engine went away"));

    await pressPutItBack();

    await waitFor(() => expect(screen.getByText(NOT_REVERTABLE)).toBeTruthy());
    expect(screen.getByText(REVERT_FAILED)).toBeTruthy();
  });

  it("and the core's own refusal renders too, with the row still in place", async () => {
    // The ordinary shape, kept so the move cannot be "delete the site and pass": a
    // resolved refusal leaves the row alone, and its sentence — the core's own, never
    // this side's — belongs under the control that was pressed.
    vi.mocked(ipc.listWorkspaceEdits).mockResolvedValue({
      value: { edits: [EDIT], truncated: false },
    });
    vi.mocked(ipc.revertWorkspaceFile).mockResolvedValueOnce({
      ok: false,
      path: EDIT.path,
      error: "That file has moved since Addison changed it.",
    });

    await pressPutItBack();

    expect(await screen.findByText("That file has moved since Addison changed it.")).toBeTruthy();
    expect(screen.getByText("put it back")).toBeTruthy();
  });

  it("and it goes when the person picks something else", async () => {
    // The other side of taking the sentence out of `RevertBlock`: it used to be
    // removed for free by whatever unmounted that block, and now it has to be
    // removed on purpose. A failure about `app.py` left standing over `README.md` is
    // the same class of wrong as no failure at all — a sentence about a file the
    // pane is not showing. Kills: dropping the clear from `openFile`.
    vi.mocked(ipc.listWorkspaceEdits).mockResolvedValue({
      value: { edits: [EDIT], truncated: false },
    });
    vi.mocked(ipc.listWorkspaceDirectory).mockImplementation(async (directory: string) => ({
      value: {
        directory,
        root: "/p",
        entries: [{ name: "README.md", kind: "file" as const, size: 10, escapes: false }],
        truncated: false,
      },
    }));
    vi.mocked(ipc.revertWorkspaceFile).mockRejectedValueOnce(new Error("engine went away"));

    await pressPutItBack();
    expect(await screen.findByText(REVERT_FAILED)).toBeTruthy();

    fireEvent.click(screen.getByText("README.md"));
    await waitFor(() => expect(screen.queryByText(REVERT_FAILED)).toBeNull());
  });
});
