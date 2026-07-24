// Workspace trust — the coding-harness trust boundary (Phase-2 step 5, contract
// D6). Five parts:
//
//   (a) The fail-closed parsers: a roots list drops any row without a usable
//       directory, a picker result that isn't a non-empty string is `null`, and a
//       mutation whose shape we can't read is {ok:false} (never a false success).
//   (b) The panel, rendered for real: the frozen standing line byte-for-byte, and
//       — load-bearing — NO false claim that the commands Addison runs are undoable
//       or restore-covered (contract D6 [F2]).
//   (c) The two-step confirm gates grantTrust: picking a folder shows the frozen
//       grant copy but does NOT grant; only the confirm click grants, with the
//       picked directory. Backing out grants nothing. A refused grant (the data-dir
//       refusal) renders as one plain sentence.
//   (d) The revoke flow: a per-row "Stop trusting" calls revokeTrust, and — driven
//       through the real hook — sets the frozen "Addison will ask first again in …"
//       line byte-for-byte.
//   (e) The page-level gate: the card renders ONLY on the Developer/Custom
//       surfaces (keyed off the active profile, never the mode); Simple never sees
//       it.

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import {
  render,
  screen,
  fireEvent,
  cleanup,
  waitFor,
  renderHook,
  act,
} from "@testing-library/react";
import {
  parseWorkspaceRoots,
  parseWorkspaceDirectory,
} from "../ipc/client";
import { WorkspaceTrustPanel } from "../components/WorkspaceTrustPanel";
import { SettingsPage } from "../components/SettingsPage";
import { useWorkspace, type WorkspaceCardState } from "../hooks/useWorkspace";
import type { ModelSelection } from "../hooks/useModelSelection";
import type { SkillsState } from "../hooks/useSkills";
import type { SnapshotsState } from "../hooks/useSnapshots";
import type { ProfileState } from "../types/ui";

// globals:false → testing-library's automatic cleanup isn't registered.
afterEach(cleanup);

// --- Frozen copy (contract D6) — byte-for-byte. -----------------------------
const STANDING_LINE =
  "Inside a trusted folder, Addison reads and edits files without asking first — " +
  "each change is logged and can be undone. Commands it runs still ask every time.";
const GRANT_CONFIRM =
  "While Addison works in this folder it won't ask before each file change, and " +
  "everything is logged. Trust this folder?";
const DATA_DIR_REFUSAL =
  "That folder holds Addison's own memory, so Addison always asks there. " +
  "Pick a project folder instead.";
const CARD_TITLE = "Folders Addison may work in";

const DIR = "/Users/me/project";

// ---------------------------------------------------------------------------
// (a) the fail-closed parsers
// ---------------------------------------------------------------------------
describe("parseWorkspaceRoots", () => {
  it("round-trips a realistic workspace.list payload", () => {
    expect(
      parseWorkspaceRoots({
        roots: [
          { directory: "/a/one", grantedAt: 1700000000 },
          { directory: "/b/two" },
        ],
      }),
    ).toEqual([
      { directory: "/a/one", grantedAt: 1700000000 },
      { directory: "/b/two", grantedAt: undefined },
    ]);
  });

  it("drops a row without a usable directory string", () => {
    const parsed = parseWorkspaceRoots({
      roots: [
        { directory: "/keep/me" },
        { directory: "" }, // empty → dropped
        { grantedAt: 123 }, // no directory → dropped
        { directory: 42 }, // non-string → dropped
        "nonsense",
      ],
    });
    expect(parsed).toEqual([{ directory: "/keep/me", grantedAt: undefined }]);
  });

  it("degrades on junk instead of throwing", () => {
    for (const junk of [null, undefined, 42, "nope", [], {}]) {
      expect(parseWorkspaceRoots(junk)).toEqual([]);
    }
  });
});

describe("parseWorkspaceDirectory", () => {
  it("returns the chosen path", () => {
    expect(parseWorkspaceDirectory({ directory: DIR })).toBe(DIR);
  });

  it("is null on a cancelled/unavailable picker (anything not a non-empty string)", () => {
    expect(parseWorkspaceDirectory({ directory: "" })).toBeNull();
    expect(parseWorkspaceDirectory({ directory: 42 })).toBeNull();
    expect(parseWorkspaceDirectory({})).toBeNull();
    expect(parseWorkspaceDirectory(null)).toBeNull();
    expect(parseWorkspaceDirectory("nope")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// (b)+(c) the panel, with an injected state (like the guard-panel tests)
// ---------------------------------------------------------------------------
function stateWith(over: Partial<WorkspaceCardState> = {}): WorkspaceCardState {
  return {
    roots: [],
    rootsLoaded: true,
    busy: false,
    error: null,
    notice: null,
    refreshWorkspace: vi.fn(),
    pickDirectory: vi.fn(async () => DIR),
    handleGrant: vi.fn(async () => true),
    handleRevoke: vi.fn(async () => {}),
    ...over,
  };
}

function renderPanel(state: WorkspaceCardState) {
  render(<WorkspaceTrustPanel connected={true} workspace={state} />);
}

describe("the workspace-trust panel", () => {
  it("shows the frozen standing line byte-for-byte", () => {
    renderPanel(stateWith());
    expect(screen.getByText(STANDING_LINE)).toBeTruthy();
  });

  it("makes NO false claim that commands are undoable or restore-covered", () => {
    renderPanel(stateWith());
    const text = document.body.textContent ?? "";
    // The one honest sentence about commands is present…
    expect(text).toContain("Commands it runs still ask every time.");
    // …and nowhere does the panel say a command can be undone or restored. If a
    // future edit softened the standing line into a false promise, this fails.
    expect(text).not.toMatch(/commands?[^.]*\b(undo|undone|restore|reverted?)\b/i);
  });

  it("shows a quiet line before the trusted folders have loaded", () => {
    renderPanel(stateWith({ rootsLoaded: false }));
    expect(screen.getByText("Looking for your trusted folders…")).toBeTruthy();
  });

  it("gates grantTrust behind the two-step confirm — pick shows the copy, only confirm grants", async () => {
    const confirmSpy = vi.spyOn(window, "confirm");
    const state = stateWith();
    renderPanel(state);

    // Step one: pick a folder. The picker resolves, the confirm appears with the
    // frozen copy and the picked path — but nothing is granted yet.
    fireEvent.click(screen.getByRole("button", { name: "Choose a folder to trust…" }));
    expect(await screen.findByText(GRANT_CONFIRM)).toBeTruthy();
    expect(screen.getByTestId("pending-dir").textContent).toBe(DIR);
    expect(state.handleGrant).not.toHaveBeenCalled();

    // Step two: confirm. Only now does grantTrust fire, with the picked directory.
    fireEvent.click(screen.getByRole("button", { name: "Trust this folder" }));
    await waitFor(() => expect(state.handleGrant).toHaveBeenCalledWith(DIR));
    // Never a browser dialog anywhere in the flow.
    expect(confirmSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("does nothing when the picker is cancelled (null)", async () => {
    const state = stateWith({ pickDirectory: vi.fn(async () => null) });
    renderPanel(state);
    fireEvent.click(screen.getByRole("button", { name: "Choose a folder to trust…" }));
    // Give the async pick a tick to settle; no confirm, no grant.
    await Promise.resolve();
    expect(screen.queryByText(GRANT_CONFIRM)).toBeNull();
    expect(state.handleGrant).not.toHaveBeenCalled();
  });

  it("lets the person back out of the grant confirm without granting", async () => {
    const state = stateWith();
    renderPanel(state);
    fireEvent.click(screen.getByRole("button", { name: "Choose a folder to trust…" }));
    await screen.findByText(GRANT_CONFIRM);
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    expect(screen.queryByText(GRANT_CONFIRM)).toBeNull();
    expect(state.handleGrant).not.toHaveBeenCalled();
  });

  it("renders the data-dir refusal as one plain sentence, not a stack trace", () => {
    renderPanel(stateWith({ error: DATA_DIR_REFUSAL }));
    const text = document.body.textContent ?? "";
    expect(text).toContain(DATA_DIR_REFUSAL);
    expect(text).not.toContain("Traceback");
    expect(text).not.toContain("Error:");
  });

  it("lists trusted roots and revokes one per row", () => {
    const state = stateWith({
      roots: [
        { directory: "/a/one", grantedAt: 1700000000 },
        { directory: "/b/two" },
      ],
    });
    renderPanel(state);
    expect(screen.getByText("/a/one")).toBeTruthy();
    expect(screen.getByText("/b/two")).toBeTruthy();
    const revokeButtons = screen.getAllByRole("button", { name: "Stop trusting" });
    expect(revokeButtons).toHaveLength(2);
    fireEvent.click(revokeButtons[1]);
    expect(state.handleRevoke).toHaveBeenCalledWith("/b/two");
  });

  it("renders a revoke notice when the hook provides one", () => {
    renderPanel(stateWith({ notice: `Addison will ask first again in ${DIR}.` }));
    expect(screen.getByText(`Addison will ask first again in ${DIR}.`)).toBeTruthy();
  });

  it("shows a quiet placeholder when the engine isn't connected", () => {
    render(<WorkspaceTrustPanel connected={false} workspace={stateWith()} />);
    expect(screen.queryByRole("button", { name: "Choose a folder to trust…" })).toBeNull();
    expect(screen.getByText(/once Addison.s engine is connected/i)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// (d) the revoke toast copy — driven through the REAL hook with mocked ipc, so
// the frozen "Addison will ask first again in …" sentence is pinned where it
// actually lives (the hook, not the panel).
// ---------------------------------------------------------------------------
vi.mock("../ipc/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../ipc/client")>();
  return {
    ...actual,
    isEngineConnected: () => true,
    subscribeCoreState: () => () => {},
    ipc: {
      ...actual.ipc,
      listWorkspaceRoots: vi.fn(async () => []),
      revokeWorkspaceTrust: vi.fn(async () => ({ ok: true })),
      grantWorkspaceTrust: vi.fn(async () => ({ ok: true })),
      pickWorkspaceDirectory: vi.fn(async () => DIR),
    },
  };
});

describe("useWorkspace (real hook, mocked ipc)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("sets the frozen revoke toast byte-for-byte on a successful revoke", async () => {
    const { result } = renderHook(() => useWorkspace({ connected: true }));
    await act(async () => {
      await result.current.handleRevoke(DIR);
    });
    expect(result.current.notice).toBe(`Addison will ask first again in ${DIR}.`);
    expect(result.current.error).toBeNull();
  });

  it("surfaces the core's plain refusal from a failed grant, and returns false", async () => {
    const { ipc } = await import("../ipc/client");
    (ipc.grantWorkspaceTrust as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      error: DATA_DIR_REFUSAL,
    });
    const { result } = renderHook(() => useWorkspace({ connected: true }));
    let landed = true;
    await act(async () => {
      landed = await result.current.handleGrant("/Users/me/.addison");
    });
    expect(landed).toBe(false);
    expect(result.current.error).toBe(DATA_DIR_REFUSAL);
  });
});

// ---------------------------------------------------------------------------
// (e) the page-level gate: the card only on Developer/Custom
// ---------------------------------------------------------------------------
const PROFILE: ProfileState = {
  activeProfile: "developer",
  mode: "open",
  profiles: [
    { id: "simple", label: "Simple", description: "Approachable by default." },
    { id: "developer", label: "Developer", description: "Power on request." },
    { id: "custom", label: "Custom", description: "Advanced.", advanced: true },
  ],
  flags: {
    exposeRoutinePlan: false,
    rawDiagnostics: false,
    headlessCli: false,
    byokFirstOnboarding: false,
  },
};

function renderSettings(profile: ProfileState, withWorkspace = true) {
  const noop = vi.fn();
  const models = {
    roles: [],
    rolesLoaded: true,
    cloudModels: [],
    providers: [],
    selectedRole: "primary",
    refreshRoles: noop,
    refreshProviders: noop,
    handleSelectModel: noop,
    handleSelectEffort: noop,
    handleChangeDefaultCloudModel: noop,
    handleChangeDefaultRole: noop,
    handleStartLocalSetup: noop,
    handleConnectProvider: noop,
    handleRemoveProvider: noop,
    localSetup: null,
    setLocalSetup: noop,
  };
  const skills = {
    skills: [],
    skillsLoaded: true,
    refreshSkills: noop,
    handleCreateSkill: vi.fn(async () => {}),
    handleUpdateSkill: vi.fn(async () => {}),
    handleToggleSkill: vi.fn(async () => {}),
    handleDeleteSkill: vi.fn(async () => {}),
  };
  const snapshots = {
    snapshots: [],
    snapshotsLoaded: true,
    busy: false,
    notice: null,
    refreshSnapshots: noop,
    handleCreateSnapshot: vi.fn(async () => {}),
    handleRestoreLastWorking: vi.fn(async () => {}),
    handleRestoreSnapshot: vi.fn(async () => {}),
    handleDeleteSnapshot: vi.fn(async () => {}),
  };
  render(
    <SettingsPage
      connected={false}
      models={models as unknown as ModelSelection}
      skills={skills as unknown as SkillsState}
      snapshots={snapshots as unknown as SnapshotsState}
      guards={stateWith() as unknown as never}
      workspace={withWorkspace ? stateWith() : undefined}
      profile={profile}
      onSetProfile={noop}
      diagnostics={[]}
      onClearDiagnostics={noop}
      theme="light"
      onSetTheme={noop}
      onOpenMenu={noop}
    />,
  );
}

describe("the workspace-trust card gate", () => {
  it("renders on the Developer surface", () => {
    renderSettings({ ...PROFILE, activeProfile: "developer", mode: "open" });
    expect(screen.getByText(CARD_TITLE)).toBeTruthy();
  });

  it("renders on the Custom surface", () => {
    renderSettings({ ...PROFILE, activeProfile: "custom", mode: "open" });
    expect(screen.getByText(CARD_TITLE)).toBeTruthy();
  });

  it("does NOT render on the Simple surface", () => {
    renderSettings({ ...PROFILE, activeProfile: "simple", mode: "safe" });
    expect(screen.queryByText(CARD_TITLE)).toBeNull();
  });

  it("is omitted when no workspace bundle is supplied (older callers)", () => {
    renderSettings({ ...PROFILE, activeProfile: "developer", mode: "open" }, false);
    expect(screen.queryByText(CARD_TITLE)).toBeNull();
  });
});
