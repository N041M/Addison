// Workspace trust — the coding-harness trust boundary (Phase-2 step 5, contract
// D6). Five parts:
//
//   (a) The fail-closed parsers: a roots list drops any row without a usable
//       directory, a picker result that isn't a non-empty string is `null`, and a
//       mutation whose shape we can't read is {ok:false} (never a false success).
//       The picker answer also carries an optional plain sentence for the one case
//       the core can name — it stopped waiting on a picker still open — which is
//       what stopped a timeout looking exactly like Cancel (2026-08-22).
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
//   (e) The page-level gate: the card renders in EVERY profile (owner decision
//       2026-08-12 — Simple gained the two path-bounded file tools on 2026-08-11 and
//       needs a way to trust the folder they scope by), it waits for the profile to
//       load, and it is omitted when no workspace bundle is supplied. The copy each
//       surface gets is the honest one for its mode.

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
import type { GuardsCardState } from "../hooks/useGuards";
import type { ProfileState } from "../types/ui";

// globals:false → testing-library's automatic cleanup isn't registered.
afterEach(cleanup);

// --- Frozen copy (contract D6) — byte-for-byte. -----------------------------
// REDESIGN NOTE (dark direction, phase 3): the filled "Choose a folder to trust…"
// button became the accent action on the empty/trailing row, worded as the brief
// words it. The frozen COPY — the standing line and the grant confirm — is
// untouched; only the label of the control that opens the OS picker moved.
const CHOOSE_ACTION = "choose a folder…";
const STANDING_LINE =
  "Inside a trusted folder, Addison reads and edits files without asking first — " +
  "each change is logged and can be undone. Commands it runs still ask every time.";
const GRANT_CONFIRM =
  "While Addison works in this folder it won't ask before each file change, and " +
  "everything is logged. Trust this folder?";
// The SAFE (Simple) pair, added 2026-08-12 with the panel's second audience. The
// OPEN copy above is UNCHANGED; these are the sentences that are true in SAFE,
// where a destructive call takes a card per invocation.
const STANDING_LINE_ASKS =
  "Inside a trusted folder, Addison can read your files and help you change them. " +
  "It asks you before every change, and every change can be undone.";
const GRANT_CONFIRM_ASKS =
  "Addison will be able to open the files in this folder, and it will ask you " +
  "before every change it makes. Trust this folder?";
const DATA_DIR_REFUSAL =
  "That folder holds Addison's own memory, so Addison always asks there. " +
  "Pick a project folder instead.";
// The step-8 fence's own refusal (rpc/workspace.py `_GRANT_AUTOMATION_DIR_REFUSAL`)
// — a second frozen sentence, because telling someone who picked
// ~/Library/LaunchAgents that the folder holds Addison's memory was false.
const AUTOMATION_DIR_REFUSAL =
  "That folder is where this computer keeps jobs it runs on a schedule, so " +
  "Addison never trusts it. Pick a project folder instead.";
// The picker's "nobody answered" sentence (rpc/workspace.py `_PICKER_TIMED_OUT`,
// 2026-08-22). It rides the SAME error line as the refusals above, which is why
// the panel needed no new slot for it.
const PICKER_TIMED_OUT =
  "Addison stopped waiting for the folder picker, so nothing was chosen and " +
  "nothing changed. Open it again and pick a folder.";
const CARD_TITLE = "Folders Addison may work in";

const DIR = "/Users/me/project";

// ---------------------------------------------------------------------------
// (a) the fail-closed parsers
// ---------------------------------------------------------------------------
// These built `{roots: […]}` by hand while the core has always sent `{folders}` —
// so the parser was tested against its own wrong assumption, agreed with itself,
// and passed while the trusted-folder list was permanently empty in the shipped
// app. The authoritative shape now lives in a generated fixture
// (parsers.fixtures.test.ts, from tests/ipc_fixtures.py); these keep the
// junk/fallback paths and use the real key.
describe("parseWorkspaceRoots", () => {
  it("round-trips a realistic workspace.list payload", () => {
    expect(
      parseWorkspaceRoots({
        folders: [
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
      folders: [
        { directory: "/keep/me" },
        { directory: "" }, // empty → dropped
        { grantedAt: 123 }, // no directory → dropped
        { directory: 42 }, // non-string → dropped
        "nonsense",
      ],
    });
    expect(parsed).toEqual([{ directory: "/keep/me", grantedAt: undefined }]);
  });

  it("reads `folders`, the key the core sends — and nothing else", () => {
    // The regression, stated as a property: a payload under any other key is not
    // a workspace list. Revert client.ts to `obj.roots` and this goes red.
    expect(parseWorkspaceRoots({ roots: [{ directory: "/a/one" }] })).toEqual([]);
    expect(parseWorkspaceRoots({ folders: [{ directory: "/a/one" }] })).toEqual([
      { directory: "/a/one", grantedAt: undefined },
    ]);
  });

  it("degrades on junk instead of throwing", () => {
    for (const junk of [null, undefined, 42, "nope", [], {}]) {
      expect(parseWorkspaceRoots(junk)).toEqual([]);
    }
  });
});

describe("parseWorkspaceDirectory", () => {
  it("returns the chosen path, with no sentence attached", () => {
    expect(parseWorkspaceDirectory({ directory: DIR })).toEqual({ directory: DIR, error: null });
  });

  it("is null on a cancelled/unavailable picker (anything not a non-empty string)", () => {
    for (const junk of [{ directory: "" }, { directory: 42 }, {}, null, "nope"]) {
      expect(parseWorkspaceDirectory(junk)).toEqual({ directory: null, error: null });
    }
  });

  it("carries the core's sentence when it stopped waiting on an open picker", () => {
    // The gap this closed: a timeout used to arrive as a bare {directory: null},
    // i.e. byte-identical to Cancel, and the person saw nothing happen at all.
    expect(parseWorkspaceDirectory({ directory: null, error: PICKER_TIMED_OUT })).toEqual({
      directory: null,
      error: PICKER_TIMED_OUT,
    });
    // Junk in `error` is not a sentence — never render a non-string as one.
    expect(parseWorkspaceDirectory({ directory: null, error: 42 })).toEqual({
      directory: null,
      error: null,
    });
    expect(parseWorkspaceDirectory({ directory: null, error: "" })).toEqual({
      directory: null,
      error: null,
    });
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

function renderPanel(state: WorkspaceCardState, asksBeforeEachChange = false) {
  render(
    <WorkspaceTrustPanel
      connected={true}
      workspace={state}
      asksBeforeEachChange={asksBeforeEachChange}
    />,
  );
}

describe("the workspace-trust panel", () => {
  it("shows the frozen standing line byte-for-byte in OPEN", () => {
    renderPanel(stateWith());
    expect(screen.getByText(STANDING_LINE)).toBeTruthy();
  });

  it("tells the SAFE truth in SAFE — a card before every change, and no OPEN copy", () => {
    // The copy decision (2026-08-12): per-mode, not one string. The OPEN line says
    // Addison edits "without asking first", which is FALSE in SAFE, where the file
    // tools card per invocation. Both directions are pinned, because the failure
    // this guards against is one sentence shown to both audiences.
    renderPanel(stateWith(), true);
    expect(screen.getByText(STANDING_LINE_ASKS)).toBeTruthy();
    expect(screen.queryByText(STANDING_LINE)).toBeNull();
    expect(document.body.textContent ?? "").not.toContain("without asking first");
  });

  it("says nothing about commands in SAFE, where Addison runs none", () => {
    renderPanel(stateWith(), true);
    expect(document.body.textContent ?? "").not.toMatch(/command/i);
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

  it("keeps the two-step ceremony in SAFE, with SAFE's own confirm copy", async () => {
    // The ceremony is identical in every profile — the OS picker, then Addison's
    // own inline confirm. Nothing about it was weakened to put the panel in Simple.
    const confirmSpy = vi.spyOn(window, "confirm");
    const state = stateWith();
    renderPanel(state, true);

    fireEvent.click(screen.getByRole("button", { name: CHOOSE_ACTION }));
    expect(await screen.findByText(GRANT_CONFIRM_ASKS)).toBeTruthy();
    expect(screen.getByTestId("pending-dir").textContent).toBe(DIR);
    expect(state.handleGrant).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Trust this folder" }));
    await waitFor(() => expect(state.handleGrant).toHaveBeenCalledWith(DIR));
    expect(confirmSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("gates grantTrust behind the two-step confirm — pick shows the copy, only confirm grants", async () => {
    const confirmSpy = vi.spyOn(window, "confirm");
    const state = stateWith();
    renderPanel(state);

    // Step one: pick a folder. The picker resolves, the confirm appears with the
    // frozen copy and the picked path — but nothing is granted yet.
    fireEvent.click(screen.getByRole("button", { name: CHOOSE_ACTION }));
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
    fireEvent.click(screen.getByRole("button", { name: CHOOSE_ACTION }));
    // Give the async pick a tick to settle; no confirm, no grant.
    await Promise.resolve();
    expect(screen.queryByText(GRANT_CONFIRM)).toBeNull();
    expect(state.handleGrant).not.toHaveBeenCalled();
  });

  it("lets the person back out of the grant confirm without granting", async () => {
    const state = stateWith();
    renderPanel(state);
    fireEvent.click(screen.getByRole("button", { name: CHOOSE_ACTION }));
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

  it("renders the automation-dir refusal as its own plain sentence", () => {
    // The fence's sentence (step 8 phase 1) — distinct from the data-dir one,
    // because the reason is different and a false reason teaches people that
    // refusals are boilerplate. Byte-for-byte with the core's frozen copy.
    renderPanel(stateWith({ error: AUTOMATION_DIR_REFUSAL }));
    const text = document.body.textContent ?? "";
    expect(text).toContain(AUTOMATION_DIR_REFUSAL);
    expect(text).not.toContain("Addison's own memory");
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
    // Each revoke names its own folder: a column of identical "Stop trusting"
    // buttons is the shape in which someone revokes the wrong one.
    const revokeButtons = screen.getAllByRole("button", { name: /^Stop trusting/ });
    expect(revokeButtons).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Stop trusting /b/two" }));
    expect(state.handleRevoke).toHaveBeenCalledWith("/b/two");
  });

  it("renders a revoke notice when the hook provides one", () => {
    renderPanel(stateWith({ notice: `Addison will ask first again in ${DIR}.` }));
    expect(screen.getByText(`Addison will ask first again in ${DIR}.`)).toBeTruthy();
  });

  it("shows a quiet placeholder when the engine isn't connected", () => {
    render(
      <WorkspaceTrustPanel
        connected={false}
        workspace={stateWith()}
        asksBeforeEachChange={false}
      />,
    );
    expect(screen.queryByRole("button", { name: CHOOSE_ACTION })).toBeNull();
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
      pickWorkspaceDirectory: vi.fn(async () => ({ directory: DIR, error: null })),
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

  it("puts the core's picker-timeout sentence on the error line, and still picks nothing", async () => {
    // The half of the gap the frontend owns. The core stopping its wait used to be
    // indistinguishable from Cancel here, so "Choose a folder…" looked like a dead
    // button. Same error line as a refused grant — no new slot, no new component.
    const { ipc } = await import("../ipc/client");
    (ipc.pickWorkspaceDirectory as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      directory: null,
      error: PICKER_TIMED_OUT,
    });
    const { result } = renderHook(() => useWorkspace({ connected: true }));
    let picked: string | null = "not-null";
    await act(async () => {
      picked = await result.current.pickDirectory();
    });
    expect(picked).toBeNull();
    expect(result.current.error).toBe(PICKER_TIMED_OUT);
  });

  it("clears a previous error when the next pick starts, and a plain cancel says nothing", async () => {
    const { ipc } = await import("../ipc/client");
    (ipc.grantWorkspaceTrust as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      error: DATA_DIR_REFUSAL,
    });
    const { result } = renderHook(() => useWorkspace({ connected: true }));
    await act(async () => {
      await result.current.handleGrant("/Users/me/.addison");
    });
    expect(result.current.error).toBe(DATA_DIR_REFUSAL);

    // A cancelled picker: {directory: null, error: null} — the sentence from the
    // last refusal must not sit there looking like this pick's outcome.
    (ipc.pickWorkspaceDirectory as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      directory: null,
      error: null,
    });
    await act(async () => {
      await result.current.pickDirectory();
    });
    expect(result.current.error).toBeNull();
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

function renderSettings(profile: ProfileState | null, withWorkspace = true, connected = false) {
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
  // The guards card is beside the workspace card on the same page but no test
  // here asserts on it, so this is the smallest HONEST GuardsCardState — nothing
  // loaded yet — rather than a cast that would let the prop drift unnoticed.
  const guards: GuardsCardState = {
    guards: null,
    guardsLoaded: false,
    busy: false,
    error: null,
    refreshGuards: noop,
    handleSave: vi.fn(async () => {}),
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
      connected={connected}
      models={models as unknown as ModelSelection}
      skills={skills as unknown as SkillsState}
      snapshots={snapshots as unknown as SnapshotsState}
      guards={guards}
      workspace={withWorkspace ? stateWith() : undefined}
      profile={profile}
      onSetProfile={noop}
      diagnostics={[]}
      onClearDiagnostics={noop}
      theme="light"
      onSetTheme={noop}
    />,
  );
}

describe("the workspace-trust card gate", () => {
  it("renders on the Developer surface", () => {
    renderSettings({ ...PROFILE, activeProfile: "developer", mode: "open" }, true, true);
    expect(screen.getByText(CARD_TITLE)).toBeTruthy();
    expect(screen.getByText(STANDING_LINE)).toBeTruthy();
  });

  it("renders on the Custom surface", () => {
    renderSettings({ ...PROFILE, activeProfile: "custom", mode: "open" }, true, true);
    expect(screen.getByText(CARD_TITLE)).toBeTruthy();
    expect(screen.getByText(STANDING_LINE)).toBeTruthy();
  });

  it("renders on the Simple surface too, in SAFE's words", () => {
    // FLIPPED 2026-08-12. This asserted the opposite ("does NOT render on the
    // Simple surface") and was right until Simple gained the two file tools, which
    // scope by trusted root — leaving a Simple-only person with no way to grant the
    // one thing their own tools need. docs/SAFETY.md invariant 1 owns the decision.
    renderSettings({ ...PROFILE, activeProfile: "simple", mode: "safe" }, true, true);
    expect(screen.getByText(CARD_TITLE)).toBeTruthy();
    expect(screen.getByText(STANDING_LINE_ASKS)).toBeTruthy();
    expect(screen.queryByText(STANDING_LINE)).toBeNull();
  });

  it("waits for the profile rather than guessing a mode for the copy", () => {
    // `null` is "not answered yet", never "Simple" — and picking a sentence from a
    // guessed mode is picking which of two contradictory promises a person reads.
    renderSettings(null);
    expect(screen.queryByText(CARD_TITLE)).toBeNull();
  });

  it("is omitted when no workspace bundle is supplied (older callers)", () => {
    renderSettings({ ...PROFILE, activeProfile: "developer", mode: "open" }, false);
    expect(screen.queryByText(CARD_TITLE)).toBeNull();
  });
});
