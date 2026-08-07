// Tool servers — the MCP client's surfaces (Phase-2 step 7, phases 1–2 of five).
// Five parts:
//
//   (a) The fail-closed parsers: a row without a usable id, name or http(s)
//       address is DROPPED, junk never throws, and every discovery field fails in
//       the direction that UNDERSTATES what Addison knows.
//   (b) The panel, rendered for real: the honest standing line byte-for-byte, the
//       per-server status line, and — load-bearing — no claim that a discovered
//       tool can be USED. Phase 2 can see a server's tools and run none of them.
//   (c) The add form, the two-press remove and "Check now", driven through the
//       real hook with mocked ipc: a refusal renders as one plain line, and a
//       check that FAILED lands on the row rather than looking like a mistake.
//   (d) The Tools surface: one SECTION per server (the plan's Decision 2), with
//       provenance, the not-runnable sentence on every tool, and a body that says
//       something for every state — unchecked, failed, and answered-with-nothing.
//   (e) The page-level gate: both surfaces render ONLY on the Developer/Custom
//       surfaces (keyed off the active profile, never the mode); Simple never
//       sees them — and the core refuses `mcp.add`/`mcp.refresh` independently.

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
import { parseMcpServers, parseMcpRefresh } from "../ipc/client";
import { McpServersPanel } from "../components/McpServersPanel";
import { SettingsPage } from "../components/SettingsPage";
import { ToolsSurface } from "../components/ToolsSurface";
import { useMcpServers, type McpServersCardState } from "../hooks/useMcpServers";
import type { ModelSelection } from "../hooks/useModelSelection";
import type { SkillsState } from "../hooks/useSkills";
import type { SnapshotsState } from "../hooks/useSnapshots";
import type { GuardsCardState } from "../hooks/useGuards";
import type { McpServer, ProfileState } from "../types/ui";

afterEach(cleanup);

// --- Frozen copy — byte-for-byte. -------------------------------------------
const STANDING_LINE =
  "A tool server is a program on the web that offers Addison extra tools. Addison can " +
  "check what a server offers and list it here — it still can't use any of those tools yet.";
const ADD_ACTION = "add a server";
const SECTION_TITLE = "Tool servers";
const DEV_ONLY =
  "Tool servers are part of the Developer profile. Switch to Developer in Settings to add one.";
/** The same sentence the core answers with (`NOT_CALLABLE_REFUSAL`). One fact,
 * told to the person and to the model in identical words. */
const NOT_RUNNABLE = "Addison can see this tool but can't use it yet.";

const URL = "https://tools.example/mcp";

/** A row in the shape the parser produces, so a test can vary one field. */
function server(over: Partial<McpServer> = {}): McpServer {
  return {
    id: "a",
    name: "Design docs",
    url: URL,
    enabled: true,
    status: "never",
    tools: [],
    ...over,
  };
}

// ---------------------------------------------------------------------------
// (a) the fail-closed parser
// ---------------------------------------------------------------------------
describe("parseMcpServers", () => {
  it("round-trips a realistic mcp.list payload", () => {
    expect(
      parseMcpServers({
        servers: [
          {
            id: "a",
            name: "Design docs",
            url: URL,
            enabled: true,
            addedAt: 1700000000,
            status: "ok",
            checkedAt: 1700000900,
            toolCount: 1,
            tools: [{ name: "search_docs", description: "Search the docs." }],
          },
          { id: "b", name: "Local", url: "http://localhost:9000" },
        ],
      }),
    ).toEqual([
      {
        id: "a",
        name: "Design docs",
        url: URL,
        enabled: true,
        addedAt: 1700000000,
        status: "ok",
        checkedAt: 1700000900,
        toolCount: 1,
        tools: [{ name: "search_docs", description: "Search the docs." }],
        skipped: undefined,
        error: undefined,
      },
      {
        id: "b",
        name: "Local",
        url: "http://localhost:9000",
        enabled: true,
        addedAt: undefined,
        // No status at all: an older core, or a payload that lost the field. The
        // answer is "never checked", never a guess in the other direction.
        status: "never",
        checkedAt: undefined,
        toolCount: undefined,
        tools: [],
        skipped: undefined,
        error: undefined,
      },
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
          // thing Addison reaches when somebody presses Check now.
          { id: "bad-scheme", name: "Sneaky", url: "file:///etc/passwd" },
          "nonsense",
        ],
      }),
    ).toEqual([
      {
        id: "keep",
        name: "Keeper",
        url: URL,
        enabled: true,
        addedAt: undefined,
        status: "never",
        checkedAt: undefined,
        toolCount: undefined,
        tools: [],
        skipped: undefined,
        error: undefined,
      },
    ]);
  });

  it("never lets a bad discovery field become a claim about what Addison found", () => {
    // Every one of these fails toward "Addison doesn't know". A parser that
    // guessed the other way would put an invented tool count, or a stranger's
    // nameless row, on the page a person reads to decide what to trust.
    const [row] = parseMcpServers({
      servers: [
        {
          id: "a",
          name: "Design docs",
          url: URL,
          status: "definitely-fine",
          checkedAt: "yesterday",
          toolCount: "lots",
          skipped: -3,
          error: { message: "nope" },
          tools: [{ name: "kept", description: "ok" }, { description: "no name" }, 7],
        },
      ],
    });
    expect(row.status).toBe("never");
    expect(row.checkedAt).toBeUndefined();
    expect(row.toolCount).toBeUndefined();
    expect(row.skipped).toBeUndefined();
    expect(row.error).toBeUndefined();
    // Tools ride only on an `ok` row: a server that did not answer has no catalog,
    // whatever the payload says it has.
    expect(row.tools).toEqual([]);
  });

  it("keeps only the named tools of a server that answered", () => {
    const [row] = parseMcpServers({
      servers: [
        {
          id: "a",
          name: "Design docs",
          url: URL,
          status: "ok",
          tools: [{ name: "kept", description: "ok" }, { description: "no name" }, 7, null],
        },
      ],
    });
    expect(row.tools).toEqual([{ name: "kept", description: "ok" }]);
  });

  it("degrades on junk instead of throwing", () => {
    for (const junk of [null, undefined, 42, "nope", [], {}]) {
      expect(parseMcpServers(junk)).toEqual([]);
    }
  });
});

describe("parseMcpRefresh", () => {
  it("keeps a check that RAN but failed separate from a check that never ran", () => {
    // The distinction is the whole point: pressing Check now on a server that is
    // switched off is not a mistake the person made, and flattening the two would
    // tell them it was.
    const ran = parseMcpRefresh({
      ok: true,
      server: { id: "a", name: "Design docs", url: URL, status: "failed", error: "No answer." },
    });
    expect(ran.ok).toBe(true);
    expect(ran.server?.status).toBe("failed");
    expect(ran.server?.error).toBe("No answer.");

    const refused = parseMcpRefresh({ ok: false, error: DEV_ONLY });
    expect(refused).toEqual({ ok: false, error: DEV_ONLY });
  });

  it("degrades an ok answer with no usable row to a plain failure", () => {
    // Better one calm line than a success the panel would have to render empty.
    expect(parseMcpRefresh({ ok: true }).ok).toBe(false);
    expect(parseMcpRefresh({ ok: true, server: { id: "a" } }).ok).toBe(false);
    for (const junk of [null, undefined, 42, "nope", []]) {
      expect(parseMcpRefresh(junk)).toEqual({ ok: false, error: undefined });
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
    checking: new Set<string>(),
    error: null,
    notice: null,
    refreshServers: vi.fn(),
    handleAdd: vi.fn(async () => true),
    handleRemove: vi.fn(async () => {}),
    handleCheck: vi.fn(async () => {}),
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

  it("never claims a discovered tool can be used", () => {
    renderPanel(
      stateWith({
        servers: [
          server({
            addedAt: 1700000000,
            status: "ok",
            checkedAt: 1700000900,
            toolCount: 3,
            tools: [{ name: "search_docs", description: "Search." }],
          }),
        ],
      }),
    );
    const text = document.body.textContent ?? "";
    // A count is now honest — a check produced it. What must never appear is any
    // suggestion Addison can USE what it found, on the one page a person checks
    // to see what their assistant is wired into.
    expect(text).toContain("3 tools found");
    expect(text).toContain("Addison can't use them yet.");
    expect(text).not.toMatch(/\b(in use|ready to use|available to Addison)\b/i);
  });

  it("says a server has not been checked rather than implying anything about it", () => {
    // The honest state for a new row AND after a restart: nothing about a check is
    // persisted, so a row Addison has not asked about says exactly that.
    renderPanel(stateWith({ servers: [server()] }));
    const text = document.body.textContent ?? "";
    expect(text).toContain("Not checked yet.");
    expect(text).not.toMatch(/\b(connected|online|reachable|offline)\b/i);
    expect(text).not.toMatch(/\d+\s+tools?\b/i);
  });

  it("shows a failed check as the core's own sentence, on the row", () => {
    renderPanel(
      stateWith({
        servers: [
          server({
            status: "failed",
            checkedAt: 1700000900,
            error: "Addison couldn't reach that server. Check the address.",
          }),
        ],
      }),
    );
    expect(
      screen.getByText("Addison couldn't reach that server. Check the address."),
    ).toBeTruthy();
  });

  it("checks one server on a press, and only that one", async () => {
    const state = stateWith({
      servers: [server(), server({ id: "b", name: "Local", url: "http://localhost:9000" })],
    });
    renderPanel(state);
    // A column of identical "Check now" buttons is the shape in which somebody
    // checks the wrong server, so each is named.
    expect(screen.getAllByRole("button", { name: /^Check .* now$/ })).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Check Local now" }));
    await waitFor(() => expect(state.handleCheck).toHaveBeenCalledWith("b"));
    expect(state.handleCheck).toHaveBeenCalledTimes(1);
  });

  it("shows a check in flight on its own row, and does not disable the others", () => {
    renderPanel(
      stateWith({
        servers: [server(), server({ id: "b", name: "Local", url: "http://localhost:9000" })],
        checking: new Set(["b"]),
      }),
    );
    expect(document.body.textContent).toContain("Checking what this server offers…");
    // One slow server must not look like a frozen page.
    expect(
      screen.getByRole("button", { name: "Check Design docs now" }).hasAttribute("disabled"),
    ).toBe(false);
    expect(screen.getByRole("button", { name: "Check Local now" }).hasAttribute("disabled")).toBe(
      true,
    );
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
      servers: [server(), server({ id: "b", name: "Local", url: "http://localhost:9000" })],
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
      refreshMcpServer: vi.fn(async () => ({ ok: true })),
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

  it("checks nothing on its own — a check is always a press", async () => {
    const { ipc } = await import("../ipc/client");
    renderHook(() => useMcpServers({ connected: true }));
    await waitFor(() => expect(ipc.listMcpServers).toHaveBeenCalled());
    // Listing is not asking. Addison makes no network request the person did not
    // just cause, so mounting the panel must never reach a server.
    expect(ipc.refreshMcpServer).not.toHaveBeenCalled();
  });

  it("applies the checked row from the answer instead of re-reading the list", async () => {
    const { ipc } = await import("../ipc/client");
    const checked = server({ status: "ok", toolCount: 1, tools: [{ name: "t", description: "" }] });
    (ipc.listMcpServers as ReturnType<typeof vi.fn>).mockResolvedValueOnce([server()]);
    (ipc.refreshMcpServer as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      server: checked,
    });
    const { result } = renderHook(() => useMcpServers({ connected: true }));
    await waitFor(() => expect(result.current.servers).toHaveLength(1));
    await act(async () => {
      await result.current.handleCheck("a");
    });
    expect(result.current.servers[0].status).toBe("ok");
    // The core just told us the newest truth about this row; a second round-trip
    // could only overwrite it with something older.
    expect(ipc.listMcpServers).toHaveBeenCalledTimes(1);
  });

  it("shows the row as checking only while the check is out", async () => {
    const { ipc } = await import("../ipc/client");
    let release: (value: { ok: boolean }) => void = () => {};
    (ipc.refreshMcpServer as ReturnType<typeof vi.fn>).mockReturnValueOnce(
      new Promise((resolve) => {
        release = resolve;
      }),
    );
    const { result } = renderHook(() => useMcpServers({ connected: true }));
    let pending: Promise<void> = Promise.resolve();
    await act(async () => {
      pending = result.current.handleCheck("a");
    });
    expect(result.current.checking.has("a")).toBe(true);
    await act(async () => {
      release({ ok: true });
      await pending;
    });
    expect(result.current.checking.has("a")).toBe(false);
  });

  it("surfaces a refusal to CHECK as a line, and leaves a failed check on the row", async () => {
    const { ipc } = await import("../ipc/client");
    (ipc.refreshMcpServer as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      error: DEV_ONLY,
    });
    const { result } = renderHook(() => useMcpServers({ connected: true }));
    await act(async () => {
      await result.current.handleCheck("a");
    });
    // The check did not RUN: that is a panel-level line.
    expect(result.current.error).toBe(DEV_ONLY);

    (ipc.refreshMcpServer as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: true,
      server: server({ status: "failed", error: "No answer." }),
    });
    await act(async () => {
      await result.current.handleCheck("a");
    });
    // A check that RAN and failed is the row's own state, not a refusal.
    expect(result.current.error).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// (d) the Tools surface — one SECTION per server (the plan's Decision 2)
// ---------------------------------------------------------------------------
function renderTools(servers: McpServer[], showTrustedFolders = true) {
  render(
    <ToolsSurface
      connected
      providers={[]}
      roles={[]}
      trustedRoots={[]}
      showTrustedFolders={showTrustedFolders}
      mcpServers={servers}
      onAddKey={vi.fn()}
      onStopTrusting={vi.fn()}
    />,
  );
}

describe("the Tools surface's tool-server sections", () => {
  it("gives each server its own section, named, rather than mixing rows into the native lists", () => {
    // Decision 2, rendered. The registry namespaces a foreign tool so it can never
    // be mistaken for a native one; this page has to draw the same line.
    renderTools([
      server({ status: "ok", tools: [{ name: "search_docs", description: "Search." }] }),
      server({ id: "b", name: "Local", url: "http://localhost:9000" }),
    ]);
    expect(screen.getByText("Tool server · Design docs")).toBeTruthy();
    expect(screen.getByText("Tool server · Local")).toBeTruthy();
  });

  it("says where a tool came from and that it cannot be run", () => {
    renderTools([
      server({
        status: "ok",
        tools: [{ name: "search_docs", description: "Search the team's documentation." }],
      }),
    ]);
    const text = document.body.textContent ?? "";
    expect(text).toContain("From your tool server Design docs.");
    expect(screen.getByText("search_docs")).toBeTruthy();
    expect(screen.getByText("Search the team's documentation.")).toBeTruthy();
    expect(screen.getByText(NOT_RUNNABLE)).toBeTruthy();
  });

  it("renders a server's text as text, never as markup", () => {
    // Everything a server sends is a stranger's text. React escapes children; this
    // is the test that fails the day somebody reaches for dangerouslySetInnerHTML
    // or routes these through the markdown renderer the thread uses.
    renderTools([
      server({
        status: "ok",
        tools: [{ name: "evil", description: "<img src=x onerror=alert(1)> **bold**" }],
      }),
    ]);
    expect(screen.getByText("<img src=x onerror=alert(1)> **bold**")).toBeTruthy();
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("strong")).toBeNull();
  });

  it("says something for every state a section can be in", () => {
    // Unchecked, unreachable and answered-with-nothing are three different facts,
    // and a silent empty section would make all three look like the fourth.
    renderTools([server()]);
    expect(document.body.textContent).toContain("Not checked yet");
    cleanup();

    renderTools([server({ status: "failed", error: "Addison couldn't reach that server." })]);
    expect(screen.getByText("Addison couldn't reach that server.")).toBeTruthy();
    cleanup();

    renderTools([server({ status: "ok", toolCount: 0, tools: [] })]);
    expect(screen.getByText("This server answered, and offered no tools.")).toBeTruthy();
  });

  it("admits when a server offered more than Addison would take", () => {
    renderTools([
      server({ status: "ok", toolCount: 1, skipped: 2, tools: [{ name: "ok_tool", description: "" }] }),
    ]);
    expect(document.body.textContent).toContain("offered 2 more Addison wouldn't take");
  });

  it("shows no tool-server section at all on the Simple surface", () => {
    // Same gate as trusted folders, and for the same reason: the Tools page is not
    // a back door to a surface Settings hides.
    renderTools([server({ status: "ok", tools: [{ name: "search_docs", description: "" }] })], false);
    expect(screen.queryByText("Tool server · Design docs")).toBeNull();
    expect(screen.queryByText("search_docs")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// (e) the page-level gate
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
