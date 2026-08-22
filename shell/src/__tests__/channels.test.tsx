// Your phone — the messaging channels' surfaces (phases 1–3, all that ship;
// docs/messaging-channel-plan.md). Five parts:
//
//   (a) The fail-closed parsers: a row without a usable id, name or known transport
//       is DROPPED, junk never throws, `tokenPresent` fails towards "unknown" —
//       never towards "no token saved" — and an unrecognised STATE fails towards
//       "stopped", never towards "listening".
//   (b) The panel, rendered for real: the PRIVACY SENTENCE byte-for-byte and FIRST,
//       the standing list of what Addison will and will not do from a phone, the
//       live status in plain words, the pairing code, and the paired-device list
//       with its Revoke.
//   (c) The DESK QUEUE, and what it deliberately is not: a note carries the person's
//       own words and the plain name of the thing Addison would have used, "Ask this
//       here" seeds the composer and asks the core for nothing at all, and there is
//       no approve, no card and no way to run a stored request.
//   (d) The page-level gate: the section renders ONLY on the Developer/Custom
//       surfaces (keyed off the active profile, never the mode); Simple never sees
//       it — and the core refuses `channel.add` outside Developer independently.
//   (e) Owner decision 8's sleep setting: what happens now is a sentence, the other
//       behaviour is the button, and an unrecognised value reads as the safe one.
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
import {
  parseChannels,
  parseChannelStatus,
  parseChannelPairings,
  parseChannelRequests,
} from "../ipc/client";
import { ChannelsPanel, PRIVACY_LINE, WHAT_IT_WILL_DO } from "../components/ChannelsPanel";
import { SettingsPage } from "../components/SettingsPage";
import { useChannels, type ChannelsCardState } from "../hooks/useChannels";
import type { ModelSelection } from "../hooks/useModelSelection";
import type { SkillsState } from "../hooks/useSkills";
import type { SnapshotsState } from "../hooks/useSnapshots";
import type { GuardsCardState } from "../hooks/useGuards";
import type { Channel, ChannelStatus, ProfileState } from "../types/ui";

afterEach(cleanup);

// --- Frozen copy — byte-for-byte. -------------------------------------------
/** THE sentence. It is repeated here in full rather than imported alone, because
 * the point of the test is that these exact words are on screen: an import would
 * follow the component wherever somebody moved the wording to. */
const PRIVACY_SENTENCE =
  "Messages you send from your phone travel through Telegram's servers, the way any " +
  "other Telegram message does. Everything else stays on this computer.";
/** The standing list — the remote floor in the person's own vocabulary. Since phase
 * 3 the floor carries three read-only tools, so the honest version names them the
 * way a person would say them and then says what happens to everything else. Frozen
 * here in full for the reason the privacy sentence is: the point of the test is that
 * these exact words are on screen. */
const STANDING_LIST =
  "From your phone, Addison answers in words, looks things up on the web, and does " +
  "the maths. Anything that changes a file, runs a command, or touches your computer " +
  "waits until you're back — Addison says so, and leaves the request here.";
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
    onWake: "decline",
    pairedDevices: 0,
    ...over,
  };
}

function status(over: Partial<ChannelStatus> = {}): ChannelStatus {
  return { state: "stopped", backoffSeconds: 0, unknownSenders: 0, ...over };
}

function stateWith(over: Partial<ChannelsCardState> = {}): ChannelsCardState {
  return {
    channels: [],
    channelsLoaded: true,
    busy: false,
    checking: null,
    error: null,
    notice: null,
    statuses: {},
    pairings: {},
    pairing: null,
    lastRemoteTurn: null,
    pendingRequests: [],
    refreshChannels: vi.fn(),
    refreshRequests: vi.fn(),
    handleDismissRequest: vi.fn(async () => {}),
    handleAdd: vi.fn(async () => true),
    handleRemove: vi.fn(async () => {}),
    handleSaveToken: vi.fn(async () => true),
    handleConnect: vi.fn(async () => {}),
    handleSetEnabled: vi.fn(async () => {}),
    handleSetOnWake: vi.fn(async () => {}),
    handleBeginPairing: vi.fn(async () => {}),
    handleCancelPairing: vi.fn(async () => {}),
    handleRevokePairing: vi.fn(async () => {}),
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
        onWake: "decline",
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
    expect(text.indexOf(PRIVACY_SENTENCE)).toBeLessThan(text.indexOf(STANDING_LIST));
    expect(text.indexOf(PRIVACY_SENTENCE)).toBeLessThan(text.indexOf("token"));
  });

  it("shows it even before the engine is connected", () => {
    // The section renders in that state too, and the sentence is the one thing on it
    // that is true whatever the engine is doing.
    render(<ChannelsPanel connected={false} channels={stateWith()} />);
    expect(screen.getByText(PRIVACY_SENTENCE)).toBeTruthy();
  });

  it("says what Addison will and will not do from a phone, and says it honestly", () => {
    // THE STANDING LIST (§3.12 item 4), and in this phase the honest version of it:
    // the remote floor is EMPTY, so a phone gets words and nothing else. Phase 3 is
    // the commit that widens both this copy and the floor, together.
    render(<ChannelsPanel connected channels={stateWith()} />);
    expect(screen.getByText(STANDING_LIST)).toBeTruthy();
    expect(WHAT_IT_WILL_DO).toBe(STANDING_LIST);
  });

  it("says Addison has not checked whether a token is saved, rather than that none is", () => {
    render(<ChannelsPanel connected channels={stateWith({ channels: [channel()] })} />);
    expect(screen.getByText("Addison hasn't checked whether a token is saved.")).toBeTruthy();
    expect(document.body.textContent ?? "").not.toContain("No token saved yet.");
    cleanup();
    // And once it HAS asked, it says what it learned rather than what it assumed.
    render(
      <ChannelsPanel
        connected
        channels={stateWith({ channels: [channel({ tokenPresent: "present" })] })}
      />,
    );
    expect(screen.getByText("A token is saved and Addison has checked it.")).toBeTruthy();
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
// (b2) the phase-2 surfaces: status, the switch, pairing, revoking
// ---------------------------------------------------------------------------
describe("the live picture", () => {
  it("says which kind of quiet this is", () => {
    // A phone that goes silent looks exactly like a phone nobody has messaged, and
    // the desk is the only place that can tell somebody which it is. Every state in
    // the core's closed vocabulary gets its own sentence, and none of them is a
    // status code.
    const lines: Array<[ChannelStatus["state"], string]> = [
      ["listening", "Listening for messages from your phone."],
      ["backing_off", "Telegram isn't answering. Addison is still trying."],
      ["token_rejected", "Telegram refused the saved token, so Addison stopped listening."],
      ["no_token", "No token saved, so there is nothing to listen with."],
      ["stopped", "Not listening."],
    ];
    for (const [state, sentence] of lines) {
      render(
        <ChannelsPanel
          connected
          channels={stateWith({
            channels: [channel()],
            statuses: { a: status({ state }) },
          })}
        />,
      );
      expect(screen.getByText(sentence)).toBeTruthy();
      cleanup();
    }
  });

  it("shows the bot it is connected as, once it has asked", () => {
    render(
      <ChannelsPanel
        connected
        channels={stateWith({
          channels: [channel()],
          statuses: { a: status({ state: "listening", connectedAs: "addison_bot" }) },
        })}
      />,
    );
    expect(document.body.textContent ?? "").toContain("connected as addison_bot");
  });

  it("counts the strangers knocking without ever quoting one", () => {
    // An unpaired phone is ignored in silence, because a reply would tell whoever
    // sent it that the bot is real and somebody is behind it. A COUNT is the only
    // trace it leaves, and the panel must not invent more than that.
    render(
      <ChannelsPanel
        connected
        channels={stateWith({
          channels: [channel()],
          statuses: { a: status({ unknownSenders: 3 }) },
        })}
      />,
    );
    expect(
      screen.getByText(
        "3 messages came from phones that aren't paired. Addison didn't reply.",
      ),
    ).toBeTruthy();
  });

  it("drives the switch from what is LISTENING, never from the saved row", () => {
    // The two questions this panel would otherwise conflate. `enabled` is what the
    // person last chose and is saved; the status is whether Addison is listening
    // right now. Nothing starts listening when the app opens, so a switched-on row
    // with a stopped service must offer "Start listening" — a switch pointing the
    // other way would be the panel telling somebody their phone is connected.
    const handleSetEnabled = vi.fn(async () => {});
    render(
      <ChannelsPanel
        connected
        channels={stateWith({
          channels: [channel({ enabled: true })],
          statuses: { a: status({ state: "stopped" }) },
          handleSetEnabled,
        })}
      />,
    );
    fireEvent.click(screen.getByLabelText("Listen to My phone"));
    expect(handleSetEnabled).toHaveBeenCalledWith(expect.objectContaining({ id: "a" }), true);
  });

  it("offers to stop when it IS listening", () => {
    const handleSetEnabled = vi.fn(async () => {});
    render(
      <ChannelsPanel
        connected
        channels={stateWith({
          channels: [channel({ enabled: false })],
          statuses: { a: status({ state: "listening" }) },
          handleSetEnabled,
        })}
      />,
    );
    fireEvent.click(screen.getByLabelText("Stop listening to My phone"));
    expect(handleSetEnabled).toHaveBeenCalledWith(expect.objectContaining({ id: "a" }), false);
  });

  it("prints the one-at-a-time refusal in the core's own words", () => {
    // Owner decision 11: v1 listens to one connection at a time, and the refusal
    // NAMES the other one, because "no" with nothing to act on is not an answer.
    const refusal =
      "Addison listens to one phone connection at a time. Switch My phone off first.";
    render(
      <ChannelsPanel
        connected
        channels={stateWith({ channels: [channel({ id: "b", name: "Tablet" })], error: refusal })}
      />,
    );
    expect(screen.getByText(refusal)).toBeTruthy();
  });

  it("asks the core to check a token, and says so while it is asking", () => {
    const handleConnect = vi.fn(async () => {});
    render(
      <ChannelsPanel
        connected
        channels={stateWith({ channels: [channel()], handleConnect })}
      />,
    );
    fireEvent.click(screen.getByLabelText("Check My phone"));
    expect(handleConnect).toHaveBeenCalled();
    cleanup();
    render(
      <ChannelsPanel
        connected
        channels={stateWith({ channels: [channel()], checking: "a" })}
      />,
    );
    expect(screen.getByText("Checking…")).toBeTruthy();
  });

  it("renders a phone turn as a notice and never as a message", () => {
    // `channel.remoteTurn` is deliberately not the streaming or activity channel: a
    // phone turn's words must never appear inside the conversation on this screen.
    // So the panel says THAT one happened, and nothing about what was said.
    render(
      <ChannelsPanel
        connected
        channels={stateWith({
          channels: [channel()],
          lastRemoteTurn: { channelId: "a", phase: "answered", at: 1 },
        })}
      />,
    );
    expect(screen.getByText("Addison answered a message from your phone.")).toBeTruthy();
  });
});

describe("pairing, on screen", () => {
  it("shows the code beside one sentence about what pairing means", () => {
    render(
      <ChannelsPanel
        connected
        channels={stateWith({
          channels: [channel()],
          pairing: { channelId: "a", code: "ABC-DEF", expiresAt: 999 },
        })}
      />,
    );
    expect(screen.getByText("ABC-DEF")).toBeTruthy();
    expect(
      screen.getByText(
        "Send this code to your bot from the phone you want to use. Only that phone will " +
          "be able to message Addison, and you can undo it here at any time.",
      ),
    ).toBeTruthy();
    // No jargon anywhere near it — personas 54 and 68 read this.
    const text = (document.body.textContent ?? "").toLowerCase();
    for (const word of ["nonce", "pairing token", "sender id", "authorization"]) {
      expect(text).not.toContain(word);
    }
  });

  it("asks for a code, and can close the window again", () => {
    const handleBeginPairing = vi.fn(async () => {});
    const handleCancelPairing = vi.fn(async () => {});
    render(
      <ChannelsPanel
        connected
        channels={stateWith({ channels: [channel()], handleBeginPairing, handleCancelPairing })}
      />,
    );
    fireEvent.click(screen.getByLabelText("Pair a phone with My phone"));
    expect(handleBeginPairing).toHaveBeenCalled();
    cleanup();
    render(
      <ChannelsPanel
        connected
        channels={stateWith({
          channels: [channel()],
          pairing: { channelId: "a", code: "ABC-DEF", expiresAt: 999 },
          handleCancelPairing,
        })}
      />,
    );
    fireEvent.click(screen.getByText("Cancel pairing"));
    expect(handleCancelPairing).toHaveBeenCalled();
  });

  it("lists paired phones and asks twice before revoking one", () => {
    // Revocation is the whole control surface a pairing has, so it is a two-press
    // confirm like every other thing that takes something away.
    const handleRevokePairing = vi.fn(async () => {});
    render(
      <ChannelsPanel
        connected
        channels={stateWith({
          channels: [channel()],
          pairings: { a: [{ id: "p1", label: "petr", pairedAt: 1_700_000_000 }] },
          handleRevokePairing,
        })}
      />,
    );
    expect(document.body.textContent ?? "").toContain("petr");
    fireEvent.click(screen.getByLabelText("Revoke petr"));
    expect(handleRevokePairing).not.toHaveBeenCalled();
    expect(screen.getByText("Really revoke?")).toBeTruthy();
    fireEvent.click(screen.getByLabelText("Revoke petr"));
    expect(handleRevokePairing).toHaveBeenCalledWith("a", "p1");
  });
});

// ---------------------------------------------------------------------------
// (a2) the phase-2 parsers, failing closed
// ---------------------------------------------------------------------------
describe("parseChannelStatus / parseChannelPairings", () => {
  it("never reads an unknown state as listening", () => {
    // The one wrong direction: an unknown state rendered as "listening" would tell
    // somebody their phone is connected when this window has no idea.
    for (const junk of [null, 42, {}, { state: "probably-fine" }, { state: "" }]) {
      expect(parseChannelStatus(junk).state).toBe("stopped");
    }
    expect(parseChannelStatus({ state: "listening" }).state).toBe("listening");
  });

  it("degrades counts and sentences without throwing", () => {
    const parsed = parseChannelStatus({
      state: "backing_off",
      backoffSeconds: "soon",
      unknownSenders: null,
      connectedAs: 7,
      error: { message: "no" },
    });
    expect(parsed).toEqual({
      state: "backing_off",
      backoffSeconds: 0,
      unknownSenders: 0,
      connectedAs: undefined,
      lastPollAt: undefined,
      error: undefined,
    });
  });

  it("drops a pairing it could not revoke and keeps one it could", () => {
    expect(
      parseChannelPairings({
        pairings: [
          { id: "p1", label: "petr", pairedAt: 5 },
          { label: "no id" },
          { id: "p2" },
          "nonsense",
        ],
      }),
    ).toEqual([
      { id: "p1", label: "petr", pairedAt: 5 },
      { id: "p2", label: "This phone", pairedAt: undefined },
    ]);
    for (const junk of [null, 42, {}, { pairings: "no" }]) {
      expect(parseChannelPairings(junk)).toEqual([]);
    }
  });

  it("keeps a note it could dismiss and drops one it could not", () => {
    expect(
      parseChannelRequests({
        requests: [
          {
            id: "r1",
            channelId: "a",
            askedAt: 5,
            toolLabel: "Change a file",
            whatWasAsked: "write hello into notes.md",
          },
          // No id: the panel would render a Dismiss it could not act on.
          { channelId: "a", toolLabel: "Change a file" },
          { id: "r2" },
          "nonsense",
        ],
      }),
    ).toEqual([
      {
        id: "r1",
        channelId: "a",
        askedAt: 5,
        toolLabel: "Change a file",
        whatWasAsked: "write hello into notes.md",
      },
      {
        id: "r2",
        channelId: "",
        askedAt: undefined,
        toolLabel: "Something Addison does at your computer",
        whatWasAsked: "",
      },
    ]);
    for (const junk of [null, 42, {}, { requests: "no" }]) {
      expect(parseChannelRequests(junk)).toEqual([]);
    }
  });

  it("ignores a tool id or arguments if a payload ever carried them", () => {
    // The shape is the guarantee: a note is a RECORD, so there is nothing on this
    // side to replay even if something upstream started sending one.
    const [parsed] = parseChannelRequests({
      requests: [
        {
          id: "r1",
          toolId: "run_command",
          args: { command: "rm -rf /" },
          toolLabel: "Run a command",
          whatWasAsked: "clean up my disk",
        },
      ],
    });
    expect(Object.keys(parsed).sort()).toEqual(
      ["askedAt", "channelId", "id", "toolLabel", "whatWasAsked"].sort(),
    );
    expect(JSON.stringify(parsed)).not.toContain("rm -rf");
  });
});

// ---------------------------------------------------------------------------
// (c) what the desk queue is, and what it deliberately is not
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// (c) the desk queue — a record, never a resumable action
// ---------------------------------------------------------------------------
const REQUEST = {
  id: "r1",
  channelId: "a",
  askedAt: 4102444800,
  toolLabel: "Change a file",
  whatWasAsked: "please write hello into notes.md",
};

describe("pending requests", () => {
  it("is absent entirely when nothing is waiting", () => {
    // A block that renders empty is a block that teaches somebody to stop reading
    // it. Nothing waiting, nothing drawn.
    render(<ChannelsPanel connected channels={stateWith({ channels: [channel()] })} />);
    expect(document.body.textContent ?? "").not.toContain("Waiting for you");
  });

  it("shows what the phone asked, in the person's own words and the tool's plain name", () => {
    render(
      <ChannelsPanel
        connected
        channels={stateWith({ channels: [channel()], pendingRequests: [REQUEST] })}
      />,
    );
    const text = document.body.textContent ?? "";
    expect(text).toContain("Waiting for you");
    expect(text).toContain("please write hello into notes.md");
    expect(text).toContain("Change a file");
    // NEVER A TOOL ID. The wire shape does not carry one; this is the second half of
    // that rule, on the screen where a person reads it.
    expect(text).not.toContain("write_project_file");
  });

  it('"Ask this here" seeds the composer and asks the core for nothing at all', async () => {
    // THE WHOLE POINT OF THE QUEUE BEING A RECORD. The button writes the person's own
    // sentence into the composer and returns to chat; they press Send, and the turn
    // runs live through the ordinary gate with the ordinary card. It does NOT dispatch
    // the stored request — there is no method on either side that could.
    //
    // Mutation: make it call an ipc method — `asked` stops being the only effect and
    // the second assertion fails.
    const onAsk = vi.fn();
    const dismiss = vi.fn(async () => {});
    const state = stateWith({
      channels: [channel()],
      pendingRequests: [REQUEST],
      handleDismissRequest: dismiss,
    });
    render(<ChannelsPanel connected channels={state} onAsk={onAsk} />);
    fireEvent.click(screen.getByRole("button", { name: /Ask this here/ }));
    expect(onAsk).toHaveBeenCalledWith("please write hello into notes.md");
    // Nothing else moved: the note is still there (only the person can clear it),
    // and no handler on the bundle was touched.
    expect(dismiss).not.toHaveBeenCalled();
    for (const handler of [
      state.handleSetEnabled,
      state.handleConnect,
      state.handleAdd,
      state.handleRemove,
    ]) {
      expect(handler).not.toHaveBeenCalled();
    }
  });

  it("offers only Dismiss when there is nowhere for a composer seed to go", () => {
    // A partial caller (no `onAsk`) gets the note and the Dismiss and no button that
    // leads nowhere — the Automations section's rule for the same seam.
    render(
      <ChannelsPanel
        connected
        channels={stateWith({ channels: [channel()], pendingRequests: [REQUEST] })}
      />,
    );
    expect(screen.queryByRole("button", { name: /Ask this here/ })).toBeNull();
    expect(screen.getByRole("button", { name: /Dismiss/ })).toBeTruthy();
  });

  it("dismisses one note by id", () => {
    const dismiss = vi.fn(async () => {});
    render(
      <ChannelsPanel
        connected
        channels={stateWith({
          channels: [channel()],
          pendingRequests: [REQUEST],
          handleDismissRequest: dismiss,
        })}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Dismiss/ }));
    expect(dismiss).toHaveBeenCalledWith("r1");
  });
});

describe("the phase-3 boundary", () => {
  it("offers no approval and no card — only asking here, and dismissing", () => {
    // The plan's "deliberately does not ship" list, as a test: no approval from a
    // phone, no card, and NO REPLAY of a queued request. Every control on this panel
    // by name — an allow-list rather than a hunt for forbidden words, because the
    // honest copy legitimately contains "connection" and "message".
    render(
      <ChannelsPanel
        connected
        onAsk={vi.fn()}
        channels={stateWith({
          channels: [channel({ tokenPresent: "present" })],
          statuses: { a: status({ state: "listening" }) },
          pairings: { a: [{ id: "p1", label: "petr" }] },
          pendingRequests: [REQUEST],
        })}
      />,
    );
    const pressable = Array.from(document.querySelectorAll("button")).map((button) =>
      (button.textContent ?? "").trim(),
    );
    expect(pressable.sort()).toEqual(
      [
        "Check now",
        "Stop listening",
        "token",
        "Remove",
        "Pair a phone",
        "Revoke",
        "Ask this here",
        "Dismiss",
        "answer late messages",
        "add a connection",
      ].sort(),
    );
    const text = (document.body.textContent ?? "").toLowerCase();
    for (const absent of ["approve", "allow this", "deny", "run it now", "do it now"]) {
      expect(text, `the panel must not offer "${absent}"`).not.toContain(absent);
    }
  });

  it("never suggests a phone can make something happen on this computer", () => {
    render(<ChannelsPanel connected channels={stateWith({ channels: [channel()] })} />);
    const text = document.body.textContent ?? "";
    expect(text).toContain(
      "Anything that changes a file, runs a command, or touches your computer waits " +
        "until you're back",
    );
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
    // The notification channel, stubbed. The real one reaches for Tauri's event
    // bridge, which is not in a jsdom window — and the hook subscribes on mount, so
    // without this every hook test below would leave an unhandled rejection behind
    // it and the suite would be reporting green over a thrown error.
    subscribe: (_method: string, handler: (params: Record<string, unknown>) => void) => {
      notificationHandlers.push(handler);
      return () => {};
    },
    ipc: {
      ...actual.ipc,
      listChannels: vi.fn(async () => []),
      addChannel: vi.fn(async () => ({ ok: true })),
      removeChannel: vi.fn(async () => ({ ok: true })),
      channelStatus: vi.fn(async () => ({
        state: "stopped",
        backoffSeconds: 0,
        unknownSenders: 0,
      })),
      listChannelPairings: vi.fn(async () => []),
      connectChannel: vi.fn(async () => ({ ok: true, connectedAs: "addison_bot" })),
      setChannelEnabled: vi.fn(async () => ({ ok: true })),
      setChannelOnWake: vi.fn(async () => ({ ok: true })),
      beginChannelPairing: vi.fn(async () => ({ ok: true, code: "ABC-DEF", expiresAt: 9 })),
      cancelChannelPairing: vi.fn(async () => ({ ok: true })),
      revokeChannelPairing: vi.fn(async () => ({ ok: true })),
      channelPendingRequests: vi.fn(async () => []),
      dismissChannelRequest: vi.fn(async () => ({ ok: true })),
    },
  };
});

/** Every handler the hook has subscribed with, so a test can push a notification
 * frame at it the way the core would. */
const notificationHandlers: Array<(params: Record<string, unknown>) => void> = [];

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

  it("reaches nothing on its own beyond reading what is already there", async () => {
    const { ipc } = await import("../ipc/client");
    (ipc.listChannels as ReturnType<typeof vi.fn>).mockResolvedValueOnce([
      { id: "a", kind: "telegram", name: "My phone", enabled: false,
        tokenPresent: "unknown", pairedDevices: 0 },
    ]);
    renderHook(() => useChannels({ connected: true }));
    await waitFor(() => expect(ipc.channelStatus).toHaveBeenCalledWith("a"));
    // MOUNTING ASKS THE CORE AND NOBODY ELSE. Reading the list, the live state and
    // the paired devices are all reads of what this computer already knows; not one
    // of them touches the keychain, and none of them reaches Telegram — checking a
    // token is a button somebody presses.
    expect(invoked).toEqual([]);
    expect(ipc.connectChannel).not.toHaveBeenCalled();
    expect(ipc.setChannelEnabled).not.toHaveBeenCalled();
  });

  it("re-reads a connection's state when the core says it moved", async () => {
    const { ipc } = await import("../ipc/client");
    (ipc.listChannels as ReturnType<typeof vi.fn>).mockResolvedValue([
      { id: "a", kind: "telegram", name: "My phone", enabled: true,
        tokenPresent: "present", pairedDevices: 1 },
    ]);
    renderHook(() => useChannels({ connected: true }));
    await waitFor(() => expect(ipc.channelStatus).toHaveBeenCalledWith("a"));
    (ipc.channelStatus as ReturnType<typeof vi.fn>).mockClear();
    // The core says the state moved. NOTHING IN THE FRAME IS AUTHORITATIVE: the
    // panel asks for the truth rather than believing the notice, so a dropped or
    // reordered frame costs a stale line and never a wrong one.
    act(() => {
      for (const handler of notificationHandlers) handler({ id: "a", state: "listening" });
    });
    await waitFor(() => expect(ipc.channelStatus).toHaveBeenCalledWith("a"));
  });

  it("surfaces the core's refusal when a second connection is switched on", async () => {
    const { ipc } = await import("../ipc/client");
    const refusal =
      "Addison listens to one phone connection at a time. Switch My phone off first.";
    (ipc.setChannelEnabled as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      error: refusal,
    });
    const { result } = renderHook(() => useChannels({ connected: true }));
    await act(async () => {
      await result.current.handleSetEnabled(
        { id: "b", kind: "telegram", name: "Tablet", enabled: false,
          tokenPresent: "present", onWake: "decline", pairedDevices: 0 },
        true,
      );
    });
    // The core's own words, verbatim — this window never writes a second sentence
    // for a refusal the core already explained.
    expect(result.current.error).toBe(refusal);
  });

  it("says which bot a checked token belongs to", async () => {
    const { ipc } = await import("../ipc/client");
    const { result } = renderHook(() => useChannels({ connected: true }));
    await act(async () => {
      await result.current.handleConnect({
        id: "a", kind: "telegram", name: "My phone", enabled: false,
        tokenPresent: "unknown", onWake: "decline", pairedDevices: 0,
      });
    });
    expect(ipc.connectChannel).toHaveBeenCalledWith("a");
    expect(result.current.notice).toBe("Connected as addison_bot.");
  });

  it("holds a pairing code in memory only, and lets it go again", async () => {
    const { ipc } = await import("../ipc/client");
    const row = {
      id: "a", kind: "telegram" as const, name: "My phone", enabled: false,
      tokenPresent: "unknown" as const, onWake: "decline" as const, pairedDevices: 0,
    };
    const { result } = renderHook(() => useChannels({ connected: true }));
    await act(async () => {
      await result.current.handleBeginPairing(row);
    });
    expect(result.current.pairing).toEqual({
      channelId: "a",
      code: "ABC-DEF",
      expiresAt: 9,
    });
    await act(async () => {
      await result.current.handleCancelPairing(row);
    });
    expect(result.current.pairing).toBeNull();
    expect(ipc.cancelChannelPairing).toHaveBeenCalledWith("a");
  });

  it("reads the desk queue on mount and again when the core says one arrived", async () => {
    const { ipc } = await import("../ipc/client");
    (ipc.channelPendingRequests as ReturnType<typeof vi.fn>).mockResolvedValue([REQUEST]);
    const { result } = renderHook(() => useChannels({ connected: true }));
    await waitFor(() => expect(result.current.pendingRequests).toHaveLength(1));
    (ipc.channelPendingRequests as ReturnType<typeof vi.fn>).mockClear();
    // The frame carries the note, and the hook asks for the list anyway: the core's
    // queue is the truth and a notification is only ever a prompt to look. A dropped
    // frame therefore costs a stale panel and never a lost request.
    act(() => {
      for (const handler of notificationHandlers) handler({ request: REQUEST });
    });
    await waitFor(() => expect(ipc.channelPendingRequests).toHaveBeenCalled());
  });

  it("dismisses a note through the core and never asks it to run one", async () => {
    const { ipc } = await import("../ipc/client");
    (ipc.channelPendingRequests as ReturnType<typeof vi.fn>).mockResolvedValue([REQUEST]);
    const { result } = renderHook(() => useChannels({ connected: true }));
    await waitFor(() => expect(result.current.pendingRequests).toHaveLength(1));
    (ipc.channelPendingRequests as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    await act(async () => {
      await result.current.handleDismissRequest("r1");
    });
    expect(ipc.dismissChannelRequest).toHaveBeenCalledWith("r1");
    await waitFor(() => expect(result.current.pendingRequests).toEqual([]));
    // THE WHOLE CORE-SIDE SURFACE A NOTE HAS. There is no third call this hook could
    // make, because there is no third method: "Ask this here" is a composer seed.
    expect(Object.keys(ipc).filter((name) => name.toLowerCase().includes("request"))).toEqual([
      "channelPendingRequests",
      "dismissChannelRequest",
    ]);
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

// ---------------------------------------------------------------------------
// (e) owner decision 8's setting — what happens to a message sent while asleep
// ---------------------------------------------------------------------------
describe("the sleep setting", () => {
  it("says what happens now, and offers the other one", () => {
    // The line is the CURRENT behaviour and the button is what pressing it would
    // change to — the idiom the rest of the panel uses ("Stop listening" over a
    // connection that is listening). Neither says "queue": what a person has is a
    // message sent while their Mac was shut.
    const onWake = vi.fn(async () => {});
    render(
      <ChannelsPanel
        connected
        channels={stateWith({ channels: [channel()], handleSetOnWake: onWake })}
      />,
    );
    expect(document.body.textContent).toContain(
      "If a message arrives while this Mac is asleep, Addison says it wasn't there",
    );
    fireEvent.click(screen.getByRole("button", { name: /answer late messages/ }));
    expect(onWake).toHaveBeenCalledWith(expect.objectContaining({ id: "a" }), "answer");
  });

  it("offers the way back when it is set to answer", () => {
    const onWake = vi.fn(async () => {});
    render(
      <ChannelsPanel
        connected
        channels={stateWith({
          channels: [channel({ onWake: "answer" })],
          handleSetOnWake: onWake,
        })}
      />,
    );
    expect(document.body.textContent).toContain(
      "Addison answers messages that arrived while this Mac was asleep.",
    );
    fireEvent.click(screen.getByRole("button", { name: /say you weren't there/ }));
    expect(onWake).toHaveBeenCalledWith(expect.objectContaining({ id: "a" }), "decline");
  });

  it("reads an unrecognised value as the safe one", async () => {
    // "decline" is the safe direction AND the core's default. Reading junk as
    // "answer" would tell somebody Addison will work through whatever was waiting —
    // the half they have to opt into.
    const [row] = parseChannels({
      channels: [
        { id: "a", kind: "telegram", name: "My phone", onWake: "whatever-you-think" },
      ],
    });
    expect(row.onWake).toBe("decline");
  });

  it("prints the core's own refusal when the profile does not allow the wider one", async () => {
    const { ipc } = await import("../ipc/client");
    (ipc.setChannelOnWake as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      error: DEV_ONLY,
    });
    const { result } = renderHook(() => useChannels({ connected: true }));
    await act(async () => {
      await result.current.handleSetOnWake(channel(), "answer");
    });
    expect(ipc.setChannelOnWake).toHaveBeenCalledWith("a", "answer");
    expect(result.current.error).toBe(DEV_ONLY);
  });
});
