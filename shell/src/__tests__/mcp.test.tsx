// Tool servers — the MCP client's configuration surface (Phase-2 step 7, phase 1
// of five). Four parts:
//
//   (a) The fail-closed parser: a row without a usable id, name or http(s) address
//       is DROPPED, and junk never throws.
//   (b) The panel, rendered for real: the honest standing line byte-for-byte, and
//       — load-bearing — NO claim that Addison is connected to, using, or getting
//       tools from a saved server. Nothing is connected in this phase.
//   (c) The add form and the two-press remove, driven through the real hook with
//       mocked ipc: a refusal (the core's Developer-only or bad-address sentence)
//       renders as one plain line and the form stays open.
//   (d) The page-level gate: the section renders ONLY on the Developer/Custom
//       surfaces (keyed off the active profile, never the mode); Simple never
//       sees it — and the core refuses `mcp.add` there independently.

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
import { parseMcpServers } from "../ipc/client";
import { McpServersPanel } from "../components/McpServersPanel";
import { SettingsPage } from "../components/SettingsPage";
import { useMcpServers, type McpServersCardState } from "../hooks/useMcpServers";
import type { ModelSelection } from "../hooks/useModelSelection";
import type { SkillsState } from "../hooks/useSkills";
import type { SnapshotsState } from "../hooks/useSnapshots";
import type { GuardsCardState } from "../hooks/useGuards";
import type { ProfileState } from "../types/ui";

afterEach(cleanup);

// --- Frozen copy — byte-for-byte. -------------------------------------------
const STANDING_LINE =
  "A tool server is a program on the web that offers Addison extra tools. Saving one " +
  "here stores its address only — Addison doesn't connect to it or use its tools yet.";
const ADD_ACTION = "add a server";
const SECTION_TITLE = "Tool servers";
const DEV_ONLY =
  "Tool servers are part of the Developer profile. Switch to Developer in Settings to add one.";

const URL = "https://tools.example/mcp";

// ---------------------------------------------------------------------------
// (a) the fail-closed parser
// ---------------------------------------------------------------------------
describe("parseMcpServers", () => {
  it("round-trips a realistic mcp.list payload", () => {
    expect(
      parseMcpServers({
        servers: [
          { id: "a", name: "Design docs", url: URL, enabled: true, addedAt: 1700000000 },
          { id: "b", name: "Local", url: "http://localhost:9000" },
        ],
      }),
    ).toEqual([
      { id: "a", name: "Design docs", url: URL, enabled: true, addedAt: 1700000000 },
      { id: "b", name: "Local", url: "http://localhost:9000", enabled: true, addedAt: undefined },
    ]);
  });

  it("drops a row it could not render a working Remove button for", () => {
    expect(
      parseMcpServers({
        servers: [
          { id: "keep", name: "Keeper", url: URL },
          { id: "", name: "No id", url: URL },
          { name: "No id at all", url: URL },
          { id: "no-name", url: URL },
          { id: "no-url", name: "No address" },
          // Not a web address: the core refuses one at the store boundary, and
          // this is the belt on those braces — the panel shows the address as the
          // thing a later phase would reach.
          { id: "bad-scheme", name: "Sneaky", url: "file:///etc/passwd" },
          "nonsense",
        ],
      }),
    ).toEqual([{ id: "keep", name: "Keeper", url: URL, enabled: true, addedAt: undefined }]);
  });

  it("degrades on junk instead of throwing", () => {
    for (const junk of [null, undefined, 42, "nope", [], {}]) {
      expect(parseMcpServers(junk)).toEqual([]);
    }
  });
});

// ---------------------------------------------------------------------------
// (b)+(c) the panel, with an injected state
// ---------------------------------------------------------------------------
function stateWith(over: Partial<McpServersCardState> = {}): McpServersCardState {
  return {
    servers: [],
    serversLoaded: true,
    busy: false,
    error: null,
    notice: null,
    refreshServers: vi.fn(),
    handleAdd: vi.fn(async () => true),
    handleRemove: vi.fn(async () => {}),
    ...over,
  };
}

function renderPanel(state: McpServersCardState) {
  render(<McpServersPanel connected={true} mcp={state} />);
}

describe("the tool-servers panel", () => {
  it("shows the honest standing line byte-for-byte", () => {
    renderPanel(stateWith());
    expect(screen.getByText(STANDING_LINE)).toBeTruthy();
  });

  it("never claims a saved server is connected, in use, or offering tools", () => {
    renderPanel(
      stateWith({
        servers: [{ id: "a", name: "Design docs", url: URL, enabled: true, addedAt: 1700000000 }],
      }),
    );
    const text = document.body.textContent ?? "";
    // The one honest sentence about what saving does…
    expect(text).toContain("Addison doesn't connect to it or use its tools yet.");
    // …and nowhere a status this phase cannot possibly know. Phase 1 has no
    // client, so "connected", "online", "3 tools" would all be fabrications on
    // the one page a person checks to see what Addison can reach.
    expect(text).not.toMatch(/\b(connected|online|reachable|offline|available tools)\b/i);
    expect(text).not.toMatch(/\d+\s+tools?\b/i);
  });

  it("shows a quiet line before the servers have loaded", () => {
    renderPanel(stateWith({ serversLoaded: false }));
    expect(screen.getByText("Looking for your tool servers…")).toBeTruthy();
  });

  it("saves a server through the form, name and address as typed", async () => {
    const state = stateWith();
    renderPanel(state);
    fireEvent.click(screen.getByRole("button", { name: ADD_ACTION }));
    fireEvent.change(screen.getByLabelText("Server name"), {
      target: { value: "  Design docs  " },
    });
    fireEvent.change(screen.getByLabelText("Server address"), { target: { value: `  ${URL} ` } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(state.handleAdd).toHaveBeenCalledWith("Design docs", URL));
  });

  it("cannot save an empty form", () => {
    const state = stateWith();
    renderPanel(state);
    fireEvent.click(screen.getByRole("button", { name: ADD_ACTION }));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(state.handleAdd).not.toHaveBeenCalled();
  });

  it("renders a refusal as one plain sentence, not a stack trace", () => {
    renderPanel(stateWith({ error: DEV_ONLY }));
    const text = document.body.textContent ?? "";
    expect(text).toContain(DEV_ONLY);
    expect(text).not.toContain("Traceback");
    expect(text).not.toContain("Error:");
  });

  it("takes two presses to remove, and names the server it is about to forget", () => {
    const state = stateWith({
      servers: [
        { id: "a", name: "Design docs", url: URL, enabled: true },
        { id: "b", name: "Local", url: "http://localhost:9000", enabled: true },
      ],
    });
    renderPanel(state);
    // A column of identical "Remove" buttons is the shape in which somebody
    // removes the wrong one, so each is named.
    expect(screen.getAllByRole("button", { name: /^Remove / })).toHaveLength(2);
    const button = screen.getByRole("button", { name: "Remove Local" });
    fireEvent.click(button);
    expect(state.handleRemove).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Remove Local" }).textContent).toBe("Really remove?");
    fireEvent.click(screen.getByRole("button", { name: "Remove Local" }));
    expect(state.handleRemove).toHaveBeenCalledWith("b", "Local");
  });

  it("shows a quiet placeholder when the engine isn't connected", () => {
    render(<McpServersPanel connected={false} mcp={stateWith()} />);
    expect(screen.queryByRole("button", { name: ADD_ACTION })).toBeNull();
    expect(screen.getByText(/once Addison.s engine is connected/i)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// (c) the hook, with mocked ipc
// ---------------------------------------------------------------------------
vi.mock("../ipc/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../ipc/client")>();
  return {
    ...actual,
    isEngineConnected: () => true,
    subscribeCoreState: () => () => {},
    ipc: {
      ...actual.ipc,
      listMcpServers: vi.fn(async () => []),
      addMcpServer: vi.fn(async () => ({ ok: true })),
      removeMcpServer: vi.fn(async () => ({ ok: true })),
    },
  };
});

describe("useMcpServers (real hook, mocked ipc)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("surfaces the core's own refusal sentence and reports the add did NOT land", async () => {
    const { ipc } = await import("../ipc/client");
    (ipc.addMcpServer as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      error: DEV_ONLY,
    });
    const { result } = renderHook(() => useMcpServers({ connected: true }));
    let landed = true;
    await act(async () => {
      landed = await result.current.handleAdd("Design docs", URL);
    });
    expect(landed).toBe(false);
    expect(result.current.error).toBe(DEV_ONLY);
  });

  it("says plainly what a successful removal did", async () => {
    const { result } = renderHook(() => useMcpServers({ connected: true }));
    await act(async () => {
      await result.current.handleRemove("a", "Design docs");
    });
    expect(result.current.notice).toBe("Addison has forgotten Design docs.");
    expect(result.current.error).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// (d) the page-level gate
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

function renderSettings(profile: ProfileState, withMcp = true) {
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
      connected={false}
      models={models as unknown as ModelSelection}
      skills={skills as unknown as SkillsState}
      snapshots={snapshots as unknown as SnapshotsState}
      guards={guards}
      mcp={withMcp ? stateWith() : undefined}
      profile={profile}
      onSetProfile={noop}
      diagnostics={[]}
      onClearDiagnostics={noop}
      theme="light"
      onSetTheme={noop}
    />,
  );
}

describe("the tool-servers section gate", () => {
  it("renders on the Developer surface", () => {
    renderSettings({ ...PROFILE, activeProfile: "developer", mode: "open" });
    expect(screen.getByText(SECTION_TITLE)).toBeTruthy();
  });

  it("renders on the Custom surface", () => {
    renderSettings({ ...PROFILE, activeProfile: "custom", mode: "open" });
    expect(screen.getByText(SECTION_TITLE)).toBeTruthy();
  });

  it("does NOT render on the Simple surface", () => {
    renderSettings({ ...PROFILE, activeProfile: "simple", mode: "safe" });
    expect(screen.queryByText(SECTION_TITLE)).toBeNull();
  });

  it("is omitted when no mcp bundle is supplied (older callers)", () => {
    renderSettings({ ...PROFILE, activeProfile: "developer", mode: "open" }, false);
    expect(screen.queryByText(SECTION_TITLE)).toBeNull();
  });
});
