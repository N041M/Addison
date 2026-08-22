// Your phone — the messaging channels' surfaces (phase 1 of three;
// docs/messaging-channel-plan.md). Four parts:
//
//   (a) The fail-closed parser: a row without a usable id, name or known transport
//       is DROPPED, junk never throws, and `tokenPresent` fails towards "unknown"
//       — never towards "no token saved", which is the one direction that would be
//       a lie about what is on somebody's computer.
//   (b) The panel, rendered for real: the PRIVACY SENTENCE byte-for-byte and FIRST,
//       the honest standing line under it, and the per-row token line.
//   (c) What phase 1 deliberately does NOT draw: no enable switch, no connect or
//       check, no pairing code, no paired-device list. A control that does nothing
//       is the panel telling somebody their phone is on.
//   (d) The page-level gate: the section renders ONLY on the Developer/Custom
//       surfaces (keyed off the active profile, never the mode); Simple never sees
//       it — and the core refuses `channel.add` outside Developer independently.
//
// G1 runs through all of it: the token field's value goes to the Rust command and
// nowhere else. The hook test below asserts it never reaches the core.

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
import { parseChannels } from "../ipc/client";
import { ChannelsPanel, PRIVACY_LINE } from "../components/ChannelsPanel";
import { SettingsPage } from "../components/SettingsPage";
import { useChannels, type ChannelsCardState } from "../hooks/useChannels";
import type { ModelSelection } from "../hooks/useModelSelection";
import type { SkillsState } from "../hooks/useSkills";
import type { SnapshotsState } from "../hooks/useSnapshots";
import type { GuardsCardState } from "../hooks/useGuards";
import type { Channel, ProfileState } from "../types/ui";

afterEach(cleanup);

// --- Frozen copy — byte-for-byte. -------------------------------------------
/** THE sentence. It is repeated here in full rather than imported alone, because
 * the point of the test is that these exact words are on screen: an import would
 * follow the component wherever somebody moved the wording to. */
const PRIVACY_SENTENCE =
  "Messages you send from your phone travel through Telegram's servers, the way any " +
  "other Telegram message does. Everything else stays on this computer.";
const STANDING_LINE =
  "Addison can't talk to your phone yet. Saving a connection here stores its name and " +
  "its token on this computer, ready for when it can.";
const SECTION_TITLE = "Your phone";
const ADD_ACTION = "add a connection";
const DEV_ONLY =
  "Connecting a phone is part of the Developer profile. Switch to Developer in Settings to set one up.";

/** A row in the shape the parser produces, so a test can vary one field. */
function channel(over: Partial<Channel> = {}): Channel {
  return {
    id: "a",
    kind: "telegram",
    name: "My phone",
    enabled: false,
    tokenPresent: "unknown",
    pairedDevices: 0,
    ...over,
  };
}

function stateWith(over: Partial<ChannelsCardState> = {}): ChannelsCardState {
  return {
    channels: [],
    channelsLoaded: true,
    busy: false,
    error: null,
    notice: null,
    refreshChannels: vi.fn(),
    handleAdd: vi.fn(async () => true),
    handleRemove: vi.fn(async () => {}),
    handleSaveToken: vi.fn(async () => true),
    ...over,
  } as ChannelsCardState;
}

// ---------------------------------------------------------------------------
// (a) the parser fails closed
// ---------------------------------------------------------------------------
describe("parseChannels", () => {
  it("keeps a usable row and drops the rest", () => {
    expect(
      parseChannels({
        channels: [
          { id: "keep", kind: "telegram", name: "My phone" },
          // No id, no name: a row the panel could not name is one it would render a
          // Remove button for and then fail to act on.
          { kind: "telegram", name: "Nameless id" },
          { id: "no-name", kind: "telegram" },
          // A transport with no adapter behind it. The panel's copy names the
          // transport, so rendering this would be the app inventing a connection.
          { id: "bridge", kind: "whatsapp", name: "Bridge" },
          "nonsense",
        ],
      }),
    ).toEqual([
      {
        id: "keep",
        kind: "telegram",
        name: "My phone",
        enabled: false,
        tokenPresent: "unknown",
        pairedDevices: 0,
        addedAt: undefined,
      },
    ]);
  });

  it("never turns a bad field into a claim about a token or a switch", () => {
    // Each of these fails towards "Addison doesn't know" and "off". `tokenPresent`
    // matters most: "absent" reads as "no token saved", and saying that about a
    // token that may well be in the keychain is the one wrong direction here.
    const [row] = parseChannels({
      channels: [
        {
          id: "a",
          kind: "telegram",
          name: "My phone",
          enabled: "yes",
          tokenPresent: "definitely-saved",
          pairedDevices: "a few",
          addedAt: "yesterday",
        },
      ],
    });
    expect(row.enabled).toBe(false);
    expect(row.tokenPresent).toBe("unknown");
    expect(row.pairedDevices).toBe(0);
    expect(row.addedAt).toBeUndefined();
  });

  it("degrades on junk instead of throwing", () => {
    for (const junk of [null, undefined, 42, "nope", [], {}]) {
      expect(parseChannels(junk)).toEqual([]);
    }
  });
});

// ---------------------------------------------------------------------------
// (b) the panel says the true thing, in the right order
// ---------------------------------------------------------------------------
describe("ChannelsPanel", () => {
  it("shows the privacy sentence, byte-for-byte", () => {
    render(<ChannelsPanel connected channels={stateWith()} />);
    expect(screen.getByText(PRIVACY_SENTENCE)).toBeTruthy();
    // The component's own export is the same string — so a rewording has to change
    // both this test and the export, which is the point of freezing it.
    expect(PRIVACY_LINE).toBe(PRIVACY_SENTENCE);
  });

  it("puts the privacy sentence FIRST, before anything about a token", () => {
    // Position is the requirement, not presence. A cost like this belongs where the
    // choice is made — above the field somebody is about to paste a token into, not
    // below it and not on a page they would have to go looking for.
    render(<ChannelsPanel connected channels={stateWith({ channels: [channel()] })} />);
    const text = document.body.textContent ?? "";
    expect(text.indexOf(PRIVACY_SENTENCE)).toBeGreaterThanOrEqual(0);
    expect(text.indexOf(PRIVACY_SENTENCE)).toBeLessThan(text.indexOf(STANDING_LINE));
    expect(text.indexOf(PRIVACY_SENTENCE)).toBeLessThan(text.indexOf("token"));
  });

  it("shows it even before the engine is connected", () => {
    // The section renders in that state too, and the sentence is the one thing on it
    // that is true whatever the engine is doing.
    render(<ChannelsPanel connected={false} channels={stateWith()} />);
    expect(screen.getByText(PRIVACY_SENTENCE)).toBeTruthy();
  });

  it("admits that nothing is connected yet", () => {
    render(<ChannelsPanel connected channels={stateWith()} />);
    expect(screen.getByText(STANDING_LINE)).toBeTruthy();
  });

  it("says Addison has not checked whether a token is saved, rather than that none is", () => {
    render(<ChannelsPanel connected channels={stateWith({ channels: [channel()] })} />);
    expect(screen.getByText("Addison hasn't checked whether a token is saved.")).toBeTruthy();
    expect(document.body.textContent ?? "").not.toContain("No token saved yet.");
  });

  it("takes a name for a new connection and saves it as a telegram channel", async () => {
    const handleAdd = vi.fn(async () => true);
    render(<ChannelsPanel connected channels={stateWith({ handleAdd })} />);
    fireEvent.click(screen.getByText(ADD_ACTION));
    fireEvent.change(screen.getByLabelText("Connection name"), {
      target: { value: "  My phone  " },
    });
    fireEvent.click(screen.getByText("Save"));
    await waitFor(() => expect(handleAdd).toHaveBeenCalledWith("telegram", "My phone"));
  });

  it("asks twice before removing", () => {
    const handleRemove = vi.fn(async () => {});
    const row = channel();
    render(<ChannelsPanel connected channels={stateWith({ channels: [row], handleRemove })} />);
    fireEvent.click(screen.getByLabelText("Remove My phone"));
    expect(handleRemove).not.toHaveBeenCalled();
    expect(screen.getByText("Really remove?")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Remove My phone"));
    expect(handleRemove).toHaveBeenCalledWith(row);
  });

  it("hands a pasted token to the save handler and clears the field", async () => {
    const handleSaveToken = vi.fn(async () => true);
    render(
      <ChannelsPanel connected channels={stateWith({ channels: [channel()], handleSaveToken })} />,
    );
    fireEvent.click(screen.getByLabelText("Save a token for My phone"));
    const field = screen.getByLabelText("My phone token") as HTMLInputElement;
    // A password field, so a token is not left legible on a screen somebody walks
    // past — the same treatment the API-key row gives a key.
    expect(field.getAttribute("type")).toBe("password");
    fireEvent.change(field, { target: { value: " 123:AAH-token " } });
    fireEvent.click(screen.getByText("Save token"));
    await waitFor(() => expect(handleSaveToken).toHaveBeenCalledWith("telegram", "123:AAH-token"));
    await waitFor(() => expect(screen.queryByLabelText("My phone token")).toBeNull());
  });

  it("says a second connection of the same kind shares the one saved token", () => {
    // The account is `channel-key:<kind>`, namespaced by TRANSPORT and not by row,
    // so two Telegram connections share one token on this computer. Pasting into the
    // second one replaces the first one's — and a surface that let that happen in
    // silence would be lying about what is stored on somebody's machine. The line
    // appears only when it is true, which is what a second row makes it.
    const two = [channel(), channel({ id: "b", name: "The kitchen tablet" })];
    render(<ChannelsPanel connected channels={stateWith({ channels: two })} />);
    fireEvent.click(screen.getByLabelText("Save a token for My phone"));
    expect(
      screen.getByText(
        "All Telegram connections on this computer share one saved token, so this " +
          "replaces it for the others too.",
      ),
    ).toBeTruthy();
    cleanup();

    render(<ChannelsPanel connected channels={stateWith({ channels: [channel()] })} />);
    fireEvent.click(screen.getByLabelText("Save a token for My phone"));
    expect(document.body.textContent ?? "").not.toContain("share one saved token");
  });

  it("prints the core's own refusal sentence rather than one of its own", () => {
    render(<ChannelsPanel connected channels={stateWith({ error: DEV_ONLY })} />);
    expect(screen.getByText(DEV_ONLY)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// (c) what phase 1 deliberately does NOT ship
// ---------------------------------------------------------------------------
describe("the phase-1 boundary", () => {
  it("draws no enable switch, no connect, and no pairing", () => {
    // The plan's "deliberately does not ship" list, as a test. Every one of these
    // would be a control with nothing behind it: there is no adapter, no poll loop
    // and no pairing anywhere in this build, so a switch would be the panel telling
    // somebody their phone is on.
    render(
      <ChannelsPanel
        connected
        channels={stateWith({ channels: [channel({ tokenPresent: "present" })] })}
      />,
    );
    // EVERY control on the panel, by name. An allow-list rather than a search for
    // forbidden words: the copy legitimately contains "connection", so a substring
    // hunt would either go red on the honest sentence or be loosened until it caught
    // nothing. What must be true is that the only things a person can PRESS here are
    // saving a connection, saving a token, and removing.
    const pressable = Array.from(document.querySelectorAll("button")).map((button) =>
      (button.textContent ?? "").trim(),
    );
    expect(pressable.sort()).toEqual(["Remove", "add a connection", "token"].sort());
    // And no switch, in either of the two shapes one takes.
    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
    // Nor any of the words a later phase's controls would arrive with.
    const text = (document.body.textContent ?? "").toLowerCase();
    for (const absent of ["turn on", "switch on", "check now", "pairing", "paired"]) {
      expect(text, `phase 1 must not offer "${absent}"`).not.toContain(absent);
    }
  });

  it("never claims a phone is paired", () => {
    render(<ChannelsPanel connected channels={stateWith({ channels: [channel()] })} />);
    expect((document.body.textContent ?? "").toLowerCase()).not.toContain("device");
  });
});

// ---------------------------------------------------------------------------
// The hook, driven for real with mocked ipc — G1 included
// ---------------------------------------------------------------------------
const invoked: Array<{ command: string; args: unknown }> = [];

vi.mock("@tauri-apps/api/core", () => ({
  invoke: vi.fn(async (command: string, args: unknown) => {
    invoked.push({ command, args });
    return undefined;
  }),
}));

vi.mock("../ipc/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../ipc/client")>();
  return {
    ...actual,
    isEngineConnected: () => true,
    subscribeCoreState: () => () => {},
    ipc: {
      ...actual.ipc,
      listChannels: vi.fn(async () => []),
      addChannel: vi.fn(async () => ({ ok: true })),
      removeChannel: vi.fn(async () => ({ ok: true })),
    },
  };
});

describe("useChannels (real hook, mocked ipc)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    invoked.length = 0;
    // `storeChannelKey` / `deleteChannelKey` are the REAL functions here — the mock
    // above spreads the actual module — so they consult the real
    // `isEngineConnected`, which asks whether Tauri is in the window. Standing this
    // up is what lets the two G1 tests below assert the actual command name and
    // arguments rather than a stub's, which is the whole value of them.
    (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
  });

  it("sends the token to the shell and never to the core (G1)", async () => {
    const { ipc } = await import("../ipc/client");
    const { result } = renderHook(() => useChannels({ connected: true }));
    await act(async () => {
      await result.current.handleSaveToken("telegram", "123:AAH-token");
    });
    // The ONE place it goes: the Rust command, keyed by transport kind.
    expect(invoked).toEqual([
      { command: "store_channel_key", args: { kind: "telegram", key: "123:AAH-token" } },
    ]);
    // And nowhere else. Every core call this hook can make is checked, because the
    // failure this guards is a token added to a core payload "just for the row" —
    // which would put it through JSON-RPC, into the core's process, and one careless
    // column away from SQLite and every later snapshot.
    for (const call of [ipc.addChannel, ipc.removeChannel, ipc.listChannels]) {
      for (const args of (call as ReturnType<typeof vi.fn>).mock.calls.flat()) {
        expect(JSON.stringify(args ?? null)).not.toContain("123:AAH-token");
      }
    }
  });

  it("deletes the token before it asks the core to drop the row", async () => {
    const { ipc } = await import("../ipc/client");
    const order: string[] = [];
    (ipc.removeChannel as ReturnType<typeof vi.fn>).mockImplementationOnce(async () => {
      order.push("channel.remove");
      return { ok: true };
    });
    const { result } = renderHook(() => useChannels({ connected: true }));
    await act(async () => {
      await result.current.handleRemove(channel());
    });
    expect(invoked.map((call) => call.command)).toEqual(["delete_channel_key"]);
    // The order is the point: the failure mode is a listed row whose token is gone
    // — visible, and removable again by pressing the same button — rather than a
    // token left on the machine belonging to a connection nothing can name.
    expect(order).toEqual(["channel.remove"]);
  });

  it("does NOT delete a token another connection of the same kind still needs", async () => {
    // The sharp edge under `channel-key:<kind>`: with two Telegram rows there is ONE
    // token, so removing either row must leave it alone. Deleting it would take the
    // remaining connection's token away silently, which is the one direction nothing
    // can put back — no snapshot has ever carried a secret.
    const { ipc } = await import("../ipc/client");
    (ipc.listChannels as ReturnType<typeof vi.fn>).mockResolvedValueOnce([
      channel(),
      channel({ id: "b", name: "The kitchen tablet" }),
    ]);
    const { result } = renderHook(() => useChannels({ connected: true }));
    await waitFor(() => expect(result.current.channels).toHaveLength(2));
    await act(async () => {
      await result.current.handleRemove(channel());
    });
    expect(invoked).toEqual([]);
    // The row still goes, which is what the person pressed.
    expect(ipc.removeChannel).toHaveBeenCalledWith("a");
  });

  it("keeps the row when the token could not be deleted", async () => {
    const { ipc } = await import("../ipc/client");
    const { invoke } = await import("@tauri-apps/api/core");
    (invoke as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      "Couldn't remove your token from the system keychain.",
    );
    const { result } = renderHook(() => useChannels({ connected: true }));
    await act(async () => {
      await result.current.handleRemove(channel());
    });
    expect(ipc.removeChannel).not.toHaveBeenCalled();
    expect(result.current.error).toContain("Couldn't remove your token");
  });

  it("surfaces the core's own refusal sentence and reports the add did NOT land", async () => {
    const { ipc } = await import("../ipc/client");
    (ipc.addChannel as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      error: DEV_ONLY,
    });
    const { result } = renderHook(() => useChannels({ connected: true }));
    let landed = true;
    await act(async () => {
      landed = await result.current.handleAdd("telegram", "My phone");
    });
    expect(landed).toBe(false);
    expect(result.current.error).toBe(DEV_ONLY);
  });

  it("reaches nothing on its own beyond reading the list", async () => {
    const { ipc } = await import("../ipc/client");
    renderHook(() => useChannels({ connected: true }));
    await waitFor(() => expect(ipc.listChannels).toHaveBeenCalled());
    // Mounting the panel must not touch the keychain or anything else: listing is
    // not asking, and in this phase there is nothing to ask anyway.
    expect(invoked).toEqual([]);
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

function renderSettings(profile: ProfileState, withChannels = true) {
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
      channels={withChannels ? stateWith() : undefined}
      profile={profile}
      onSetProfile={noop}
      diagnostics={[]}
      onClearDiagnostics={noop}
      theme="light"
      onSetTheme={noop}
    />,
  );
}

describe("the Settings section", () => {
  it("renders on the Developer surface", () => {
    renderSettings({ ...PROFILE, activeProfile: "developer", mode: "open" });
    expect(screen.getByText(SECTION_TITLE)).toBeTruthy();
  });

  it("renders on the Custom surface", () => {
    renderSettings({ ...PROFILE, activeProfile: "custom", mode: "open" });
    expect(screen.getByText(SECTION_TITLE)).toBeTruthy();
  });

  it("does NOT render on the Simple surface", () => {
    // Channels are dev-only for v1 (owner decision 10). A Settings surface for a
    // capability a profile lacks is profile surface, not a disabled artifact —
    // nobody's saved work is being hidden, because in Simple there is none.
    renderSettings({ ...PROFILE, activeProfile: "simple", mode: "safe" });
    expect(screen.queryByText(SECTION_TITLE)).toBeNull();
    expect(document.body.textContent ?? "").not.toContain(PRIVACY_SENTENCE);
  });

  it("is omitted when no channels bundle is supplied (older callers)", () => {
    renderSettings({ ...PROFILE, activeProfile: "developer", mode: "open" }, false);
    expect(screen.queryByText(SECTION_TITLE)).toBeNull();
  });
});
