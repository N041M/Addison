// The review surface — the Developer/Custom screen that shows what Addison changed
// and puts one file back. (Phase-3 review-surface plan Build §4.)
//
// Two halves, for two different kinds of failure:
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
// MONACO IS MOCKED, always. It cannot run in jsdom (it needs real layout,
// `ResizeObserver` and `matchMedia`), so the dynamic `import("../lib/monaco")` is
// stubbed and the assertions are about the SURFACE — never about the editor's
// internals, which is the right place for the line anyway.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor, act } from "@testing-library/react";
import { App } from "../App";
import { CodeSurface } from "../components/CodeSurface";
import { Sidebar } from "../components/Sidebar";
import type { CodeReviewState } from "../hooks/useCodeReview";
import type { WorkspaceEdit, WorkspaceListing } from "../types/protocol";

// --- Monaco, stubbed. Nothing below asserts anything about it. ---------------
vi.mock("../lib/monaco", () => {
  const model = { dispose: () => {} };
  const instance = {
    getModel: () => null,
    setModel: () => {},
    updateOptions: () => {},
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
    isEngineConnected: () => true,
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
    "Addison changed this before the app was last restarted, so it can't put it " +
    "back for you. The earlier version is on the left; you can copy it.";

  it("says so in the plan's words and offers NO control", () => {
    // The restart problem, made honest. The shell's ledger of files it has written
    // is session-scoped on purpose and is empty after a restart, while the database
    // rows survive — so this screen would otherwise render a Revert next to every
    // historic edit and every one of them would fail. Kills: the dead-button
    // regression, and kills softening the sentence into "can't be undone".
    renderSurface(reviewState({ edits: [{ ...EDIT, revertable: false }] }));
    expect(screen.getByText(READ_ONLY_LINE)).toBeTruthy();
    expect(screen.queryByText("put it back")).toBeNull();
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

  it("dims a row that leads outside the trusted folder, and does not let it be clicked", () => {
    // The honesty affordance, not the boundary — the boundary is the core refusing
    // the follow-up call. Kills: rendering an escaping entry as an ordinary row,
    // which a person opens before anything refuses.
    renderSurface(reviewState());
    expect(screen.getByText(ESCAPES)).toBeTruthy();
    const row = screen.getByText("link");
    expect(row.closest("button")).toBeNull();
    expect(row.className).toContain("line-through");
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
});
