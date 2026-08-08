// Automations — the work Addison writes down for THIS COMPUTER to run (Phase-2
// step 8, phases 2, 3 and 4: authoring, arming, then state honesty + Simple).
// Eight parts:
//
//   (a) The fail-closed parser: a row without a usable id, name or COMMAND is
//       dropped, junk never throws, and a missing schedule sentence falls back to
//       the core's own "No schedule saved yet." rather than to anything this side
//       assembled out of the numbers. Plus `automation.status`, which fails closed
//       toward "arming isn't available here" — the direction that cannot invent a
//       capability this computer does not have.
//   (b) The section, rendered for real: the core's schedule sentence printed as it
//       arrives, the whole command, and one armed/not-armed line per row.
//   (c) Remove: two presses, named after its own automation, the right id on the
//       wire, and the list re-read afterwards — because the core mints a restore
//       point and can refuse, so what is on screen after a press is a guess until
//       it has asked again.
//   (d) The page: the section renders in EVERY profile now. Phase 3 hid it outside
//       Developer/Custom; phase 4 replaced the gate with the listed-but-disabled
//       treatment the 2026-08-06 artifact decision requires, so what the profile
//       decides is what a row may DO, never whether somebody's saved work is on
//       screen at all.
//   (e) ARMED-NESS COMES FROM THE OPERATING SYSTEM (plan §5.6). Asked once when the
//       section loads, never stored and never polled; a row that was never answered
//       for says NOTHING about armed-ness rather than the comfortable half. Arm is
//       offered only where arming exists, Disarm only for what the OS says is armed,
//       and neither one calls anything: they write a sentence into the composer,
//       because the ceremony belongs on the card and a settings button that installs
//       a recurring job is the reflex the ceremony exists to break.
//   (f) SIMPLE: rows LISTED and visibly inert, carrying the core's own sentence
//       verbatim — no Arm, no Disarm, and no command text (the developer vocabulary
//       SAFETY.md keeps off that surface), but Remove kept, because a tightening
//       must never be trapped by a profile switch, and the armed line kept, because
//       a job armed in Developer keeps running after the switch.
//   (g) THE HOOK: `useAutomations` reads the ROWS when App mounts it and asks the
//       operating system nothing — "when the surface loads" must not become "every
//       time Addison opens" (plan §5.6).
//   (h) THE RESTORE PATH: `automations` is a snapshot-captured table, so App's
//       `onRestored` closure re-reads it with every other captured table — and does
//       NOT re-ask the OS, which a restore cannot have changed.
//
// The generated fixture (fixtures/automation.list.json, produced by
// tests/ipc_fixtures.py from the real handler) is consumed by
// parsers.fixtures.test.ts — that is where this parser meets a payload nobody on
// this side wrote.

import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { act, render, renderHook, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { parseAutomations, parseAutomationStatus } from "../ipc/client";
import { AutomationsSection, SettingsPage } from "../components/SettingsPage";
import { useAutomations, type AutomationsCardState } from "../hooks/useAutomations";
import type { ModelSelection } from "../hooks/useModelSelection";
import type { SkillsState } from "../hooks/useSkills";
import type { SnapshotsState } from "../hooks/useSnapshots";
import type { GuardsCardState } from "../hooks/useGuards";
import type { Automation, AutomationStatus } from "../types/protocol";
import type { ProfileState } from "../types/ui";

afterEach(cleanup);

// --- Frozen copy — byte-for-byte. -------------------------------------------
/** What a row says when the OS is not running it. Phase 2 said "…once you arm it"
 * while arming did not exist; the sentence flips in the commit that makes arming
 * real (plan §7) and still promises nothing. */
const NOT_ARMED = "Not armed — nothing runs until you arm it.";
/** What a row says when the OS IS running it — the same truth, in the same words,
 * as the arming card's own warning. */
const ARMED =
  "Armed — your computer runs this on its own schedule, even when Addison is closed.";
/** Off macOS: one plain sentence, and no Arm action anywhere. */
const UNSUPPORTED =
  "Arming isn't available on this computer, so these stay written down and never run.";
/** When the OS could not be asked at all. */
const STATUS_UNKNOWN = "Addison couldn't check which of these your computer is running.";
/** What the Arm / Disarm actions write into the composer for the person to send. */
const ARM_ASK = 'Arm the automation "Tidy up downloads".';
const DISARM_ASK = 'Disarm the automation "Tidy up downloads".';
const EMPTY = "No automations yet. Ask Addison to set one up.";
const LOADING = "Looking for your automations…";
const SECTION_TITLE = "Automations";
/** The core's own words for a row whose schedule says nothing (agent_core/
 * automations.py `schedule_sentence`). The frontend falls back to this string and
 * never to a schedule of its own invention. */
const NO_SCHEDULE = "No schedule saved yet.";

const COMMAND = "/usr/bin/find ~/Downloads -mtime +30 -delete";

/** The marker the core puts on a row the active profile can't use — in Simple,
 * every row, because an automation runs a command. THE CORE OWNS THESE WORDS; what
 * is pinned on this side is that the sentence is rendered exactly as it arrived and
 * that this surface writes none of its own. */
const WAITING_REASON = "developer_abilities";
const WAITING_MESSAGE =
  "That automation runs a command, so it's waiting in Developer profile.";

/** A row in the shape the parser produces, so a test can vary one field. */
function automation(over: Partial<Automation> = {}): Automation {
  return {
    id: "a",
    name: "Tidy up downloads",
    label: "com.addison.auto.tidy-downloads",
    command: COMMAND,
    scheduleKind: "interval",
    schedule: { minutes: 60 },
    scheduleSentence: "Every hour",
    createdInMode: "open",
    createdAt: 1700000000,
    ...over,
  };
}

/** The section wired to its REAL hook — the shape App renders, and the shape the
 * phase-2/3 tests below still drive through their ipc mocks. `developerSurface`
 * defaults to true because that is the surface those tests were written against;
 * part (f) flips it to false and asserts the Simple treatment. */
function Section({
  connected = true,
  developerSurface = true,
  onAsk,
}: {
  connected?: boolean;
  developerSurface?: boolean;
  onAsk?: (text: string) => void;
}) {
  const automations = useAutomations({ connected });
  return (
    <AutomationsSection
      connected={connected}
      automations={automations}
      developerSurface={developerSurface}
      onAsk={onAsk}
    />
  );
}

vi.mock("../ipc/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../ipc/client")>();
  return {
    ...actual,
    isEngineConnected: () => true,
    subscribeCoreState: () => () => {},
    ipc: {
      ...actual.ipc,
      listAutomations: vi.fn(async () => [] as Automation[]),
      removeAutomation: vi.fn(async () => ({ ok: true })),
      getAutomationStatus: vi.fn(async () => ({ armed: [], supported: true }) as AutomationStatus),
      disarmOrphanAutomation: vi.fn(async () => ({ ok: true })),
    },
  };
});

// ---------------------------------------------------------------------------
// (a) the fail-closed parser
// ---------------------------------------------------------------------------
describe("parseAutomations", () => {
  it("round-trips a realistic automation.list payload", () => {
    expect(
      parseAutomations({
        automations: [
          {
            id: "a",
            name: "Tidy up downloads",
            label: "com.addison.auto.tidy-downloads",
            command: COMMAND,
            scheduleKind: "interval",
            schedule: { minutes: 60 },
            scheduleSentence: "Every hour",
            createdInMode: "open",
            createdAt: 1700000000,
          },
          {
            id: "b",
            name: "Back up notes",
            label: "com.addison.auto.backup-notes",
            command: "/usr/local/bin/backup-notes",
            scheduleKind: "calendar",
            schedule: { hour: 7, minute: 30, weekday: 1 },
            scheduleSentence: "Every Monday at 7:30",
            createdInMode: "open",
            createdAt: 1700000100,
          },
        ],
      }),
    ).toEqual([
      automation(),
      {
        id: "b",
        name: "Back up notes",
        label: "com.addison.auto.backup-notes",
        command: "/usr/local/bin/backup-notes",
        scheduleKind: "calendar",
        schedule: { hour: 7, minute: 30, weekday: 1 },
        scheduleSentence: "Every Monday at 7:30",
        createdInMode: "open",
        createdAt: 1700000100,
      },
    ]);
  });

  it("drops a row it could not render a working Remove button for", () => {
    // Same reasoning as the trusted-folder and tool-server parsers: the control is
    // named after the row, so a row that cannot be named is one the section would
    // offer a button for and then fail to act on.
    expect(
      parseAutomations({
        automations: [
          { id: "keep", name: "Keeper", command: COMMAND, scheduleSentence: "Every hour" },
          { id: "", name: "No id", command: COMMAND },
          { name: "No id at all", command: COMMAND },
          { id: "no-name", command: COMMAND },
          "nonsense",
          42,
          null,
        ],
      }).map((row) => row.id),
    ).toEqual(["keep"]);
  });

  it("drops a row that cannot say what would run", () => {
    // The command IS the automation. A row without one would render as a schedule
    // with no consequence attached — a line that says something happens every
    // morning and cannot say what.
    expect(
      parseAutomations({
        automations: [
          { id: "no-command", name: "Mystery", scheduleSentence: "Every hour" },
          { id: "empty-command", name: "Mystery", command: "", scheduleSentence: "Every hour" },
          { id: "not-a-string", name: "Mystery", command: ["rm", "-rf"] },
        ],
      }),
    ).toEqual([]);
  });

  it("never invents a schedule for a row that did not carry a sentence", () => {
    // The failure that matters is in one direction only: telling somebody a command
    // runs hourly when the core never said so. Missing, empty or non-string all land
    // on the core's own "no schedule" line — and the NUMBERS are still carried, so
    // the day something wants them they are there and honest.
    const rows = parseAutomations({
      automations: [
        { id: "a", name: "A", command: COMMAND, schedule: { minutes: 60 } },
        { id: "b", name: "B", command: COMMAND, scheduleSentence: "" },
        { id: "c", name: "C", command: COMMAND, scheduleSentence: { text: "hourly" } },
      ],
    });
    expect(rows.map((row) => row.scheduleSentence)).toEqual([
      NO_SCHEDULE,
      NO_SCHEDULE,
      NO_SCHEDULE,
    ]);
    expect(rows[0].schedule).toEqual({ minutes: 60 });
  });

  it("keeps only numbers in the schedule, and only a kind it knows", () => {
    const [row] = parseAutomations({
      automations: [
        {
          id: "a",
          name: "A",
          command: COMMAND,
          scheduleKind: "whenever-i-feel-like-it",
          schedule: { minutes: 60, note: "run this first", weekday: null },
          scheduleSentence: "Every hour",
          createdInMode: "sideways",
          createdAt: "yesterday",
        },
      ],
    });
    expect(row.scheduleKind).toBeUndefined();
    expect(row.schedule).toEqual({ minutes: 60 });
    expect(row.createdInMode).toBeUndefined();
    expect(row.createdAt).toBeUndefined();
    // The sentence still speaks: the core rendered it, and a kind this build has
    // never heard of is no reason to withhold what the core said about it.
    expect(row.scheduleSentence).toBe("Every hour");
  });

  it("keeps the core's unavailable marker, unknown reasons included", () => {
    // The row is LISTED and disabled, not hidden (owner decision 2026-08-06), and
    // `reason` is an open slug vocabulary the core owns — a cause this build has
    // never heard of must not be read as "the row is fine".
    const rows = parseAutomations({
      automations: [
        {
          id: "a",
          name: "A",
          command: COMMAND,
          unavailable: { reason: WAITING_REASON, message: WAITING_MESSAGE },
        },
        {
          id: "b",
          name: "B",
          command: COMMAND,
          unavailable: { reason: "a_reason_from_2027", message: WAITING_MESSAGE },
        },
      ],
    });
    expect(rows[0].unavailable).toEqual({ reason: WAITING_REASON, message: WAITING_MESSAGE });
    expect(rows[1].unavailable).toEqual({
      reason: "a_reason_from_2027",
      message: WAITING_MESSAGE,
    });
  });

  it("treats a marker that cannot say WHY as absent, never as a disabled row", () => {
    // Fail-closed in the direction that cannot leave somebody staring at their own
    // saved work sitting inert with no explanation — the "where did my stuff go?"
    // bug wearing a different hat. The row stays usable-looking; what actually
    // refuses is dispatch, which never consults this field.
    const rows = parseAutomations({
      automations: [
        { id: "a", name: "A", command: COMMAND, unavailable: { reason: WAITING_REASON } },
        { id: "b", name: "B", command: COMMAND, unavailable: { message: "" } },
        { id: "c", name: "C", command: COMMAND, unavailable: { message: "   " } },
        { id: "d", name: "D", command: COMMAND, unavailable: { message: 42 } },
        { id: "e", name: "E", command: COMMAND, unavailable: "waiting" },
        { id: "f", name: "F", command: COMMAND, unavailable: 7 },
        { id: "g", name: "G", command: COMMAND, unavailable: null },
      ],
    });
    expect(rows).toHaveLength(7);
    for (const row of rows) expect(row.unavailable).toBeUndefined();
  });

  it("never invents a marker for a row that did not carry one", () => {
    // The absence of the field is the shape of every usable row and of every payload
    // from an older core. This side must not decide that a row is unavailable — only
    // render that the core said so.
    const [row] = parseAutomations({
      automations: [{ id: "a", name: "A", command: COMMAND, createdInMode: "open" }],
    });
    expect(row.unavailable).toBeUndefined();
    expect("unavailable" in row).toBe(false);
  });

  it("degrades on junk instead of throwing", () => {
    for (const junk of [null, undefined, 42, "nope", [], {}, { automations: "lots" }]) {
      expect(parseAutomations(junk)).toEqual([]);
    }
  });
});

describe("parseAutomationStatus", () => {
  it("round-trips what the operating system answered", () => {
    expect(
      parseAutomationStatus({
        armed: ["com.addison.auto.tidy-downloads", "com.addison.auto.backup-notes"],
        supported: true,
      }),
    ).toEqual({
      armed: ["com.addison.auto.tidy-downloads", "com.addison.auto.backup-notes"],
      supported: true,
    });
  });

  it("keeps the core's own sentence when there is one", () => {
    expect(parseAutomationStatus({ armed: [], supported: false, error: "Not on this Mac." })).toEqual(
      { armed: [], supported: false, error: "Not on this Mac." },
    );
    // An empty string is not a sentence — the surface's own line is better than a
    // blank row where an explanation should be.
    expect(parseAutomationStatus({ armed: [], supported: false, error: "" }).error).toBeUndefined();
  });

  it("fails closed toward 'arming isn't available here'", () => {
    // The direction matters: `supported` decides whether an Arm control exists at
    // all, so anything that is not exactly `true` must read as "no". A truthy string
    // is the shape that would otherwise conjure a capability out of a typo.
    for (const junk of [null, undefined, 42, "nope", [], {}, { supported: "true" }, { armed: 1 }]) {
      expect(parseAutomationStatus(junk)).toEqual({ armed: [], supported: false });
    }
  });

  it("keeps only labels it could match a row against", () => {
    expect(
      parseAutomationStatus({ armed: ["ok", "", 7, null, { label: "x" }], supported: true }).armed,
    ).toEqual(["ok"]);
  });
});

// ---------------------------------------------------------------------------
// (b)+(c) the section, driven through the real component with mocked ipc
// ---------------------------------------------------------------------------
describe("the Automations section", () => {
  /** App's `seedAsk`: writes a sentence into the composer and returns to chat. */
  const onAsk = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  /** The section with a list and an OS answer behind it. The default answer is
   * "arming works here, nothing is armed" — the ordinary Developer machine. */
  async function renderSection(
    rows: Automation[],
    status: AutomationStatus | "unreachable" = { armed: [], supported: true },
  ) {
    const { ipc } = await import("../ipc/client");
    (ipc.listAutomations as ReturnType<typeof vi.fn>).mockResolvedValue(rows);
    const statusMock = ipc.getAutomationStatus as ReturnType<typeof vi.fn>;
    if (status === "unreachable") {
      statusMock.mockRejectedValue(new Error("engine went away"));
    } else {
      statusMock.mockResolvedValue(status);
    }
    render(<Section onAsk={onAsk} />);
    if (rows.length > 0) {
      await screen.findByText(rows[0].name);
    } else {
      await screen.findByText(EMPTY);
    }
    return ipc;
  }

  it("shows the name, the core's schedule sentence, the whole command and the standing line", async () => {
    await renderSection([automation()]);
    expect(screen.getByText("Tidy up downloads")).toBeTruthy();
    expect(screen.getByText("Every hour")).toBeTruthy();
    // WHOLE and unshortened: the command is what the keyword ceremony (phase 3)
    // exists to make somebody read, and a truncated one defeats it at its one moment.
    expect(screen.getByText(COMMAND)).toBeTruthy();
    expect(screen.getByText(NOT_ARMED)).toBeTruthy();
  });

  it("prints the sentence the core sent rather than one built from the numbers", async () => {
    // The row below is deliberately incoherent — the words say one thing, the
    // numbers another. Only the core's words may reach the screen: a second
    // renderer on this side is how a surface ends up saying "Every day at 7:5" on
    // the one line a person reads before letting a command run while they sleep.
    await renderSection([
      automation({ scheduleSentence: "Every Monday at 7:30", schedule: { minutes: 5 } }),
    ]);
    expect(screen.getByText("Every Monday at 7:30")).toBeTruthy();
    expect(document.body.textContent ?? "").not.toMatch(/every 5 minutes/i);
  });

  it("says a row is not armed, and never that it is scheduled or running", async () => {
    await renderSection([automation(), automation({ id: "b", name: "Back up notes" })]);
    const text = document.body.textContent ?? "";
    // One line per ROW: it is a statement about that automation's state, and phase 4
    // replaces it with what the OS actually answers, row by row.
    expect(screen.getAllByText(NOT_ARMED)).toHaveLength(2);
    expect(text).not.toMatch(/\b(scheduled|running|installed|active)\b/i);
  });

  it("says there are none, and how to get one", async () => {
    await renderSection([]);
    expect(screen.getByText(EMPTY)).toBeTruthy();
    // There is deliberately no "New automation" button: one is written by asking.
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("does not claim there are none before it has asked", async () => {
    const { ipc } = await import("../ipc/client");
    // A fetch that never settles — a slow first answer must read as "looking", not
    // as a claim about the person's own saved work.
    (ipc.listAutomations as ReturnType<typeof vi.fn>).mockReturnValue(new Promise(() => {}));
    render(<Section />);
    expect(screen.getByText(LOADING)).toBeTruthy();
    expect(screen.queryByText(EMPTY)).toBeNull();
  });

  it("asks nothing at all while the engine is down, and says so", async () => {
    const { ipc } = await import("../ipc/client");
    render(<Section connected={false} />);
    expect(screen.getByText(/once Addison.s engine is connected/i)).toBeTruthy();
    expect(ipc.listAutomations).not.toHaveBeenCalled();
    // Including the operating system: an unreachable engine is not a reason to go
    // asking launchd what it holds.
    expect(ipc.getAutomationStatus).not.toHaveBeenCalled();
    expect(screen.queryByText(EMPTY)).toBeNull();
  });

  it("takes two presses to remove, names the automation, and sends its id", async () => {
    const ipc = await renderSection([
      automation(),
      automation({ id: "b", name: "Back up notes", command: "/usr/local/bin/backup-notes" }),
    ]);
    // A column of identical "Remove" buttons is the shape in which somebody removes
    // the wrong one.
    expect(screen.getAllByRole("button", { name: /^Remove / })).toHaveLength(2);
    const button = screen.getByRole("button", { name: "Remove Back up notes" });
    fireEvent.click(button);
    expect(ipc.removeAutomation).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Remove Back up notes" }).textContent).toBe(
      "Really remove?",
    );
    fireEvent.click(screen.getByRole("button", { name: "Remove Back up notes" }));
    await waitFor(() => expect(ipc.removeAutomation).toHaveBeenCalledWith("b"));
    expect(ipc.removeAutomation).toHaveBeenCalledTimes(1);
  });

  it("re-reads the list after a removal instead of trusting the screen", async () => {
    // The core mints a restore point for a removal and REFUSES the whole thing if it
    // cannot — so what is on screen after a press is a guess until it has asked again.
    // Two reads before the press: the hook's own at mount, and the section's at load.
    const ipc = await renderSection([automation()]);
    expect(ipc.listAutomations).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByRole("button", { name: "Remove Tidy up downloads" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove Tidy up downloads" }));
    await waitFor(() => expect(ipc.listAutomations).toHaveBeenCalledTimes(3));
  });

  it("re-reads the saved rows when the section loads, so a row authored in chat is on screen", async () => {
    // `create_automation` writes rows from CHAT, and the hook read the list when App
    // mounted it — possibly long before. This page unmounts between visits, so the
    // section re-reads on load; without that read (found by the post-merge review of
    // phase 4), the automation somebody just asked Addison for stayed missing from
    // the very screen they open to see it until a restart, a removal or a restore —
    // "where did my stuff go?", on the surface that exists to answer it. The OS ask
    // is a different question with a different cadence and is pinned separately.
    const { ipc } = await import("../ipc/client");
    (ipc.listAutomations as ReturnType<typeof vi.fn>).mockResolvedValue([]);
    (ipc.getAutomationStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
      armed: [],
      supported: true,
    });
    // The app's own shape: the hook lives in App, the section mounts per visit.
    function AppShape({ visiting }: { visiting: boolean }) {
      const bundle = useAutomations({ connected: true });
      if (!visiting) return null;
      return (
        <AutomationsSection
          connected={true}
          automations={bundle}
          developerSurface={true}
          onAsk={onAsk}
        />
      );
    }
    const { rerender } = render(<AppShape visiting={false} />);
    // Launch: the hook reads the rows once. Nobody is looking yet.
    await waitFor(() => expect(ipc.listAutomations).toHaveBeenCalledTimes(1));
    // Chat: Addison authors a row. The hook has no way to hear about it.
    (ipc.listAutomations as ReturnType<typeof vi.fn>).mockResolvedValue([automation()]);
    // The visit: opening Settings re-reads, and the new row is simply there.
    rerender(<AppShape visiting={true} />);
    await screen.findByText("Tidy up downloads");
    expect(ipc.listAutomations).toHaveBeenCalledTimes(2);
  });

  it("renders a refusal as the core's own plain sentence, not a stack trace", async () => {
    const ipc = await renderSection([automation()]);
    (ipc.removeAutomation as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      error: "That automation isn't saved any more.",
    });
    fireEvent.click(screen.getByRole("button", { name: "Remove Tidy up downloads" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove Tidy up downloads" }));
    expect(await screen.findByText("That automation isn't saved any more.")).toBeTruthy();
    const text = document.body.textContent ?? "";
    expect(text).not.toContain("Traceback");
    expect(text).not.toContain("Error:");
  });

  it("says something plain when a removal cannot be sent at all", async () => {
    const ipc = await renderSection([automation()]);
    (ipc.removeAutomation as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error("boom"));
    fireEvent.click(screen.getByRole("button", { name: "Remove Tidy up downloads" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove Tidy up downloads" }));
    expect(
      await screen.findByText("Addison couldn't remove that automation just now."),
    ).toBeTruthy();
    expect(document.body.textContent ?? "").not.toContain("boom");
  });

  it("renders a command as text, never as markup", async () => {
    // A command is text somebody (or a model, at phase 2's authoring door) wrote.
    // React escapes children; this is the test that fails the day somebody reaches
    // for dangerouslySetInnerHTML or routes these through the markdown renderer.
    await renderSection([
      automation({ command: "<img src=x onerror=alert(1)> && echo **bold**" }),
    ]);
    expect(screen.getByText("<img src=x onerror=alert(1)> && echo **bold**")).toBeTruthy();
    expect(document.querySelector("img")).toBeNull();
    expect(document.querySelector("strong")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// (e) armed-ness — the OPERATING SYSTEM's answer, and the arm/disarm affordance
// ---------------------------------------------------------------------------
describe("what the Automations section says is armed", () => {
  const onAsk = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  async function renderSection(
    rows: Automation[],
    status: AutomationStatus | "unreachable" = { armed: [], supported: true },
  ) {
    const { ipc } = await import("../ipc/client");
    (ipc.listAutomations as ReturnType<typeof vi.fn>).mockResolvedValue(rows);
    const statusMock = ipc.getAutomationStatus as ReturnType<typeof vi.fn>;
    if (status === "unreachable") {
      statusMock.mockRejectedValue(new Error("engine went away"));
    } else {
      statusMock.mockResolvedValue(status);
    }
    render(<Section onAsk={onAsk} />);
    await screen.findByText(rows[0].name);
    return ipc;
  }

  const TIDY = automation();
  const BACKUP = automation({
    id: "b",
    name: "Back up notes",
    label: "com.addison.auto.backup-notes",
    command: "/usr/local/bin/backup-notes",
  });

  it("asks the operating system once, when the section loads", async () => {
    // Never stored, never polled, never checked at startup (plan §5.6): a G3 restore
    // can put a ROW back and can never put a JOB back, so the OS is asked on arrival
    // and then left alone.
    const ipc = await renderSection([TIDY]);
    expect(ipc.getAutomationStatus).toHaveBeenCalledTimes(1);
    await new Promise((r) => setTimeout(r, 20));
    expect(ipc.getAutomationStatus).toHaveBeenCalledTimes(1);
  });

  it("says a row is armed when the OS is holding it, and not armed when it isn't", async () => {
    // The row does not know. Its LABEL is what the OS answers about, which is why a
    // restore, a reinstall or somebody deleting the plist by hand all land here
    // honestly with no special case.
    await renderSection([TIDY, BACKUP], {
      armed: ["com.addison.auto.backup-notes"],
      supported: true,
    });
    expect(screen.getByText(ARMED)).toBeTruthy();
    expect(screen.getByText(NOT_ARMED)).toBeTruthy();
    expect(screen.getAllByText(ARMED)).toHaveLength(1);
  });

  it("offers Arm on a row nothing is running, named after its own automation", async () => {
    await renderSection([TIDY, BACKUP], { armed: [], supported: true });
    expect(screen.getByRole("button", { name: "Arm Tidy up downloads" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Arm Back up notes" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Disarm / })).toBeNull();
  });

  it("offers Disarm ONLY for what the OS says is armed", async () => {
    await renderSection([TIDY, BACKUP], {
      armed: ["com.addison.auto.tidy-downloads"],
      supported: true,
    });
    expect(screen.getByRole("button", { name: "Disarm Tidy up downloads" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Arm Tidy up downloads" })).toBeNull();
    // …and the other row is untouched by its neighbour's state.
    expect(screen.getByRole("button", { name: "Arm Back up notes" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Disarm Back up notes" })).toBeNull();
  });

  it("arms by ASKING, never by calling: the sentence goes to the composer", async () => {
    // There is no `automation.arm` on the Frontend→Core surface and deliberately
    // never was one. Arming is a tool the gate cards with a typed code; a settings
    // button that installed a recurring job would be exactly the reflex the ceremony
    // exists to break.
    await renderSection([TIDY], { armed: [], supported: true });
    fireEvent.click(screen.getByRole("button", { name: "Arm Tidy up downloads" }));
    expect(onAsk).toHaveBeenCalledWith(ARM_ASK);
  });

  it("disarms by asking too", async () => {
    await renderSection([TIDY], { armed: ["com.addison.auto.tidy-downloads"], supported: true });
    fireEvent.click(screen.getByRole("button", { name: "Disarm Tidy up downloads" }));
    expect(onAsk).toHaveBeenCalledWith(DISARM_ASK);
  });

  it("says arming isn't available on this computer, and offers no Arm", async () => {
    // Off macOS. One plain sentence, the seatbelt's temperament — and no control that
    // would only ever refuse.
    await renderSection([TIDY], { armed: [], supported: false });
    expect(screen.getByText(UNSUPPORTED)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Arm / })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Disarm / })).toBeNull();
    // The row is still honest about its own state, and still removable.
    expect(screen.getByText(NOT_ARMED)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Remove Tidy up downloads" })).toBeTruthy();
  });

  it("treats a core sentence as the answer, and an ERROR as no answer at all", async () => {
    // The core keeps three outcomes apart on purpose: this computer cannot arm, the
    // OS answered, and Addison could not find out. The third arrives as an empty
    // list WITH a sentence beside it, and reading that as "nothing is armed" would
    // tell somebody their automation was off while it was running.
    await renderSection([TIDY], {
      armed: [],
      supported: false,
      error: "Addison couldn't ask this computer just now.",
    });
    expect(screen.getByText("Addison couldn't ask this computer just now.")).toBeTruthy();
    expect(screen.queryByText(UNSUPPORTED)).toBeNull();
    const text = document.body.textContent ?? "";
    expect(text).not.toContain(NOT_ARMED);
    expect(text).not.toContain(ARMED);
    expect(screen.queryByRole("button", { name: /^Arm / })).toBeNull();
  });

  it("claims nothing about a row when the OS could not be asked", async () => {
    // The comfortable half of an answer nobody got is the failure this section cannot
    // afford: a surface quietly reading "not armed" over a job that is running.
    await renderSection([TIDY], "unreachable");
    expect(screen.getByText(STATUS_UNKNOWN)).toBeTruthy();
    const text = document.body.textContent ?? "";
    expect(text).not.toContain(NOT_ARMED);
    expect(text).not.toContain(ARMED);
    expect(screen.queryByRole("button", { name: /^Arm / })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Disarm / })).toBeNull();
  });

  it("offers no arming affordance at all when there is nowhere for it to lead", async () => {
    // A partial caller (no `onAsk`) still renders the list and the truth about it;
    // what it does not render is a button that goes nowhere.
    const { ipc } = await import("../ipc/client");
    (ipc.listAutomations as ReturnType<typeof vi.fn>).mockResolvedValue([TIDY]);
    (ipc.getAutomationStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
      armed: [],
      supported: true,
    });
    render(<Section />);
    await screen.findByText(TIDY.name);
    expect(screen.getByText(NOT_ARMED)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Arm / })).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// (d) the page — every profile, and what each one may do with a row
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

/** A fabricated `useAutomations` bundle — the page tests are about what the PAGE
 * hands the section, so they never run the hook. */
function automationsState(over: Partial<AutomationsCardState> = {}): AutomationsCardState {
  return {
    automations: [],
    automationsLoaded: true,
    automationsFailed: false,
    status: null,
    statusFailed: false,
    busy: false,
    error: null,
    refreshAutomations: vi.fn(),
    refreshArmedState: vi.fn(),
    handleRemove: vi.fn(async () => {}),
    handleDisarmOrphan: vi.fn(async () => {}),
    ...over,
  };
}

function renderSettings(profile: ProfileState, automations = automationsState()) {
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
      connected={true}
      models={models as unknown as ModelSelection}
      skills={skills as unknown as SkillsState}
      snapshots={snapshots as unknown as SnapshotsState}
      guards={guards}
      automations={automations}
      profile={profile}
      onSetProfile={noop}
      diagnostics={[]}
      onClearDiagnostics={noop}
      theme="light"
      onSetTheme={noop}
      // App's `seedAsk`. Without it the Arm / Disarm actions are not offered at all
      // (there is nowhere for them to lead), so a page test about which surface
      // offers them has to provide one.
      onAskAddison={noop}
    />,
  );
}

/** One saved row and an OS that says it is running it — the state a profile switch
 * has to stay honest about. */
const ARMED_STATE = automationsState({
  automations: [automation({ unavailable: { reason: WAITING_REASON, message: WAITING_MESSAGE } })],
  status: { armed: ["com.addison.auto.tidy-downloads"], supported: true },
});

describe("the Automations section on every surface", () => {
  it("renders on the Developer surface", () => {
    renderSettings({ ...PROFILE, activeProfile: "developer", mode: "open" });
    expect(screen.getByText(SECTION_TITLE)).toBeTruthy();
  });

  it("renders on the Custom surface", () => {
    // Custom is Developer plus tunable prompting guards; every Developer surface is
    // one it also has.
    renderSettings({ ...PROFILE, activeProfile: "custom", mode: "open" });
    expect(screen.getByText(SECTION_TITLE)).toBeTruthy();
  });

  it("renders on the Simple surface too, listing the row and saying why it waits", () => {
    // Phase 3 hid this section outside Developer/Custom. That is the failure the
    // 2026-08-06 artifact decision reversed: switching profile emptied a page of
    // somebody's own saved work, and the only honest reading available to them was
    // that Addison had deleted it. The row is listed, inert, and says why — in the
    // core's own sentence.
    renderSettings({ ...PROFILE, activeProfile: "simple", mode: "safe" }, ARMED_STATE);
    expect(screen.getByText(SECTION_TITLE)).toBeTruthy();
    expect(screen.getByText("Tidy up downloads")).toBeTruthy();
    expect(screen.getByText(WAITING_MESSAGE)).toBeTruthy();
  });

  it("says nothing to Simple that invites the capability Simple cannot have", () => {
    // BOTH SENTENCES WERE NEW TO SIMPLE IN PHASE 4, and both invited arming: the
    // empty state said "Ask Addison to set one up" (for a tool that is `open_only`
    // and can only answer with a refusal), and a listed row said "nothing runs until
    // you arm it" — a second-person instruction directly under the line explaining
    // that this profile cannot. SAFETY.md names the shape: "a vocabulary that
    // teaches one, an affordance that invites one" (phase-4 review).
    //
    // Mutation: render AUTOMATIONS_EMPTY or AUTOMATION_NOT_ARMED unconditionally.
    const simple = { ...PROFILE, activeProfile: "simple" as const, mode: "safe" as const };

    renderSettings(simple, automationsState({ automations: [] }));
    expect(screen.getByText("No automations saved.")).toBeTruthy();
    expect(screen.queryByText(/Ask Addison to set one up/)).toBeNull();
    cleanup();

    renderSettings(
      simple,
      automationsState({
        automations: [
          automation({ unavailable: { reason: WAITING_REASON, message: WAITING_MESSAGE } }),
        ],
        status: { armed: [], supported: true },
      }),
    );
    expect(screen.getByText("Not running.")).toBeTruthy();
    expect(screen.queryByText(/until you arm it/)).toBeNull();
  });

  it("keeps both of those sentences on the Developer surface, where they are true", () => {
    // The precision half: the fix must not blunt the copy for the profile that CAN
    // act on it. "Ask Addison to set one up" is exactly the right instruction there.
    renderSettings(PROFILE, automationsState({ automations: [] }));
    expect(screen.getByText(/Ask Addison to set one up/)).toBeTruthy();
    cleanup();

    renderSettings(
      PROFILE,
      automationsState({
        automations: [automation()],
        status: { armed: [], supported: true },
      }),
    );
    expect(screen.getByText(/until you arm it/)).toBeTruthy();
  });

  it("never reads a row's created_in_mode stamp to decide what it may offer", () => {
    // THE CLAIM THE COMMENT BELOW USED TO MAKE WITH NOTHING BEHIND IT (phase-4
    // review). Every automation fixture in this file is stamped "open", so the
    // mutation `usable = … && automation.createdInMode === "open"` was true for
    // every row in every rendering test and survived the whole suite — the exact
    // shape the Python side guards with its `_SAFE_STAMPED` row and an AST scan.
    //
    // A row STAMPED "safe" cannot be written by any tool, but a hand edit or a
    // restore can produce one. On the Developer surface it must be fully usable:
    // the stamp says where a thing was born, never what it needs.
    const stamped = automation({ createdInMode: "safe" });
    renderSettings(
      { ...PROFILE, activeProfile: "developer", mode: "open" },
      automationsState({ automations: [stamped], status: { armed: [], supported: true } }),
    );

    // Fully usable on the Developer surface, stamp notwithstanding.
    expect(screen.getByText(stamped.command)).toBeTruthy();
    expect(screen.getByRole("button", { name: new RegExp(`^Arm ${stamped.name}`) })).toBeTruthy();
    expect(screen.queryByText(WAITING_MESSAGE)).toBeNull();
  });

  it("hands Simple no arming controls and no command text, from the PAGE", () => {
    // The page is what answers "which profile is this" — the section never asks, and
    // never reads a row's `created_in_mode` stamp (the routines gap in KNOWN-GAPS is
    // the cautionary entry: the stamp says where a thing was born, not what it needs).
    renderSettings({ ...PROFILE, activeProfile: "simple", mode: "safe" }, ARMED_STATE);
    expect(screen.queryByRole("button", { name: /^Arm / })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Disarm / })).toBeNull();
    expect(document.body.textContent ?? "").not.toContain(COMMAND);
    // …and the two things Simple keeps: the way out, and the truth.
    expect(screen.getByRole("button", { name: "Remove Tidy up downloads" })).toBeTruthy();
    expect(screen.getByText(ARMED)).toBeTruthy();
  });

  it("gives the SAME row its command and its arming control on the Developer surface", () => {
    // The regression guard for the profile prop: one state object, two surfaces, and
    // the difference is exactly what the profile decides.
    renderSettings(
      { ...PROFILE, activeProfile: "developer", mode: "open" },
      automationsState({
        automations: [automation()],
        status: { armed: [], supported: true },
      }),
    );
    expect(screen.getByText(COMMAND)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Arm Tidy up downloads" })).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// (f) Simple — listed, inert, and saying why (the artifact rule)
// ---------------------------------------------------------------------------
describe("the Automations section in Simple", () => {
  const onAsk = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  /** Simple's surface: `developerSurface` false, and rows carrying the core's
   * marker — which is what the core sends in Simple, on every row, because an
   * automation runs a command. */
  async function renderSimple(
    rows: Automation[],
    status: AutomationStatus = { armed: [], supported: true },
  ) {
    const { ipc } = await import("../ipc/client");
    (ipc.listAutomations as ReturnType<typeof vi.fn>).mockResolvedValue(rows);
    (ipc.getAutomationStatus as ReturnType<typeof vi.fn>).mockResolvedValue(status);
    render(<Section developerSurface={false} onAsk={onAsk} />);
    await screen.findByText(rows[0].name);
    return ipc;
  }

  const WAITING = automation({
    unavailable: { reason: WAITING_REASON, message: WAITING_MESSAGE },
  });

  it("lists the row and prints the core's reason verbatim", async () => {
    await renderSimple([WAITING]);
    expect(screen.getByText("Tidy up downloads")).toBeTruthy();
    expect(screen.getByText("Every hour")).toBeTruthy();
    // Byte for byte, as it arrived. This side never rewrites the sentence and never
    // writes one of its own: the surface and the refusal must tell one story.
    expect(screen.getByText(WAITING_MESSAGE)).toBeTruthy();
    // Visibly inert, in the routine library's own annotation.
    expect(screen.getByText("Waiting")).toBeTruthy();
  });

  it("never prints the command on the Simple surface", async () => {
    // SAFETY.md's own line about what a disabled row may show: "a command widget's
    // command text is not printed in the Simple rail". An automation's command is
    // the same vocabulary, and the reason it IS printed in Developer — the typed
    // code exists to make somebody read it before arming — has no counterpart on a
    // surface that cannot arm.
    await renderSimple([WAITING]);
    expect(document.body.textContent ?? "").not.toContain(COMMAND);
    expect(screen.queryByText(COMMAND)).toBeNull();
  });

  it("offers no way to arm or disarm — for a row the OS is running or one it isn't", async () => {
    // BOTH branches, in one test on purpose: the Disarm control only exists for an
    // armed row and the Arm control only for an idle one, so a single row can only
    // ever prove half of this, and the half it does not prove is a live control on
    // the Simple surface.
    await renderSimple(
      [
        WAITING,
        automation({
          id: "b",
          name: "Back up notes",
          label: "com.addison.auto.backup-notes",
          unavailable: { reason: WAITING_REASON, message: WAITING_MESSAGE },
        }),
      ],
      { armed: ["com.addison.auto.tidy-downloads"], supported: true },
    );
    expect(screen.queryByRole("button", { name: /^Arm / })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Disarm / })).toBeNull();
    expect(onAsk).not.toHaveBeenCalled();
  });

  it("keeps Remove, because a profile switch must never trap what somebody wants gone", async () => {
    // Removing is a TIGHTENING and answers in every profile (plan §1, phase 1) — and
    // it is the one way a Simple person can stop a job their computer is running,
    // because the core disarms a row before it forgets it.
    const ipc = await renderSimple([WAITING]);
    fireEvent.click(screen.getByRole("button", { name: "Remove Tidy up downloads" }));
    expect(ipc.removeAutomation).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Remove Tidy up downloads" }));
    await waitFor(() => expect(ipc.removeAutomation).toHaveBeenCalledWith("a"));
  });

  it("still says what the computer is running", async () => {
    // A job armed in Developer keeps running after the switch, and this is the
    // surface that would otherwise say nothing about it. Same sentence, unchanged.
    await renderSimple([WAITING], {
      armed: ["com.addison.auto.tidy-downloads"],
      supported: true,
    });
    expect(screen.getByText(ARMED)).toBeTruthy();
  });

  it("asks the operating system in Simple as well", async () => {
    // Asking is what makes the line above possible. The alternative — not asking
    // outside Developer — would leave Simple silent about a job that is running,
    // which is the one thing this section may not be.
    const ipc = await renderSimple([WAITING]);
    expect(ipc.getAutomationStatus).toHaveBeenCalledTimes(1);
  });

  it("never invents a reason for a row the core did not mark", async () => {
    // The marker is the CORE's. An unmarked row is rendered as one, with no sentence
    // and no Waiting tag — and still with nothing to arm it with, because the
    // profile answers that question independently of the marker.
    await renderSimple([automation()]);
    expect(screen.queryByText("Waiting")).toBeNull();
    expect(document.body.textContent ?? "").not.toContain(WAITING_MESSAGE);
    expect(screen.queryByRole("button", { name: /^Arm / })).toBeNull();
    expect(document.body.textContent ?? "").not.toContain(COMMAND);
  });

  it("disables a MARKED row on the Developer surface too", async () => {
    // Either answer alone is enough to make a row inert. The marker's vocabulary is
    // open — a later cause needs no new field — so a reason this build has never
    // heard of must still disable the row rather than be shrugged off.
    const { ipc } = await import("../ipc/client");
    (ipc.listAutomations as ReturnType<typeof vi.fn>).mockResolvedValue([
      automation({ unavailable: { reason: "some_future_reason", message: WAITING_MESSAGE } }),
    ]);
    (ipc.getAutomationStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
      armed: [],
      supported: true,
    });
    render(<Section onAsk={onAsk} />);
    await screen.findByText("Tidy up downloads");
    expect(screen.getByText(WAITING_MESSAGE)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Arm / })).toBeNull();
    expect(screen.getByRole("button", { name: "Remove Tidy up downloads" })).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// (g) the hook — App owns this state, and mounting it checks nothing
// ---------------------------------------------------------------------------
describe("useAutomations", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("reads the saved rows when App mounts it", async () => {
    const { ipc } = await import("../ipc/client");
    (ipc.listAutomations as ReturnType<typeof vi.fn>).mockResolvedValue([automation()]);
    const { result } = renderHook(() => useAutomations({ connected: true }));
    await waitFor(() => expect(result.current.automations).toHaveLength(1));
    expect(result.current.automationsLoaded).toBe(true);
  });

  it("asks the operating system NOTHING on mount — nothing checks at startup", async () => {
    // The hook is mounted by App at launch. Asking launchd here would quietly turn
    // "asked when the surface loads" (plan §5.6) into "asked every time Addison
    // opens", which is the mcp temperament's own rule broken by a refactor.
    const { ipc } = await import("../ipc/client");
    renderHook(() => useAutomations({ connected: true }));
    await waitFor(() => expect(ipc.listAutomations).toHaveBeenCalled());
    expect(ipc.getAutomationStatus).not.toHaveBeenCalled();
  });

  it("asks the operating system when the SECTION loads, and once", async () => {
    const { ipc } = await import("../ipc/client");
    (ipc.listAutomations as ReturnType<typeof vi.fn>).mockResolvedValue([automation()]);
    render(<Section />);
    await screen.findByText("Tidy up downloads");
    expect(ipc.getAutomationStatus).toHaveBeenCalledTimes(1);
  });

  it("reads nothing at all while the engine is down", async () => {
    const { ipc } = await import("../ipc/client");
    renderHook(() => useAutomations({ connected: false }));
    await new Promise((r) => setTimeout(r, 10));
    expect(ipc.listAutomations).not.toHaveBeenCalled();
    expect(ipc.getAutomationStatus).not.toHaveBeenCalled();
  });

  it("re-reads the list after a removal instead of trusting what it had", async () => {
    const { ipc } = await import("../ipc/client");
    const { result } = renderHook(() => useAutomations({ connected: true }));
    await waitFor(() => expect(ipc.listAutomations).toHaveBeenCalledTimes(1));
    await act(async () => {
      await result.current.handleRemove("a");
    });
    expect(ipc.removeAutomation).toHaveBeenCalledWith("a");
    expect(ipc.listAutomations).toHaveBeenCalledTimes(2);
  });

  it("surfaces the core's own refusal sentence rather than one of its own", async () => {
    const { ipc } = await import("../ipc/client");
    (ipc.removeAutomation as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      error: "That automation isn't saved any more.",
    });
    const { result } = renderHook(() => useAutomations({ connected: true }));
    await act(async () => {
      await result.current.handleRemove("a");
    });
    expect(result.current.error).toBe("That automation isn't saved any more.");
  });

  it("keeps 'could not find out' apart from 'nothing is armed'", async () => {
    // A failed ask leaves `status` null, which the section reads as "say nothing
    // about armed-ness" — never as the comfortable half of an answer nobody got.
    const { ipc } = await import("../ipc/client");
    (ipc.getAutomationStatus as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error("engine went away"),
    );
    const { result } = renderHook(() => useAutomations({ connected: true }));
    await act(async () => {
      result.current.refreshArmedState();
    });
    await waitFor(() => expect(result.current.statusFailed).toBe(true));
    expect(result.current.status).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// (h) a restore puts this list back with everything else
// ---------------------------------------------------------------------------
// `automations` IS a snapshot-captured table, so restoring a snapshot taken before
// one was written deletes that row core-side — and a restore taken while one
// existed puts it back. App re-reads every other captured table on `onRestored`;
// this one was self-fetching and was not re-read, so Settings went on offering
// Remove for a row the core had already forgotten (the tool-server bug, one table
// along).
//
// The subject is the WIRING, which has no unit to render: the closure lives in App
// and every call inside it is a different hook's. So this reads the file, the same
// way mcp.test.tsx pins its own entry.
describe("the restore path", () => {
  // Read as TEXT and off the cwd, not `import.meta.url`: under vitest's transform
  // that resolves to a virtual URL, and the app's own module is not what is being
  // checked — the line in it is.
  const APP = readFileSync(join(process.cwd(), "src", "App.tsx"), "utf8");

  function restoreBody(): string {
    const restore = /onRestored:\s*\(\)\s*=>\s*\{([\s\S]*?)\n\s*\},/.exec(APP);
    expect(restore, "App no longer has an onRestored closure to check").toBeTruthy();
    return restore![1];
  }

  it("re-reads the saved automations along with every other restored table", () => {
    const body = restoreBody();
    // The company it must keep.
    for (const call of ["refreshProfile", "refreshWidgets", "refreshServers"]) {
      expect(body, `a restore must re-read ${call}`).toContain(call);
    }
    expect(body, "a restore must re-read the automations").toContain("refreshAutomations");
  });

  it("does NOT re-ask the operating system, because a restore cannot have armed anything", () => {
    // There is no armed column to restore and a one-action restore cannot perform
    // the keyword ceremony (plan §5.6), so what launchd holds is what it held a
    // moment ago. Asking again here would be a check nobody caused.
    //
    // THIS IS ALSO WHY AN ORPHAN APPEARS ON THE NEXT SECTION LOAD RATHER THAN AT
    // ONCE, and the latency is the accepted cost (2026-08-08). A restore CAN orphan a
    // job — the capture is REPLACE-ALL, so restoring a point from before an
    // automation deletes its row while the job file stays installed — and with the
    // rows re-read but the OS not re-asked, the section is holding a stale armed set
    // for as long as it stays mounted. Closing that window would mean re-asking here,
    // which is the check §5.6 forbids and which would be wrong on its own terms.
    expect(restoreBody()).not.toContain("refreshArmedState");
    expect(restoreBody()).not.toContain("disarmOrphan");
  });
});

// ---------------------------------------------------------------------------
// (i) THE ORPHAN — a job the computer runs that no row can reach (2026-08-08)
// ---------------------------------------------------------------------------
// A G3 restore is REPLACE-ALL, so restoring a point from before an automation was
// written deletes its row while `<label>.plist` stays installed and launchd goes on
// running it at every login. The row was the only thing that could NAME that job or
// reach it with a control, so it became invisible and unstoppable (KNOWN-GAPS, closed).
//
// RECONCILE-ON-RESTORE: the section already has both answers — what the OS says it is
// running, and what is saved — so it compares them and renders what is left over. It
// does that at LOAD, never on a timer and never during the restore itself; a restore is
// never blocked and nothing is silently disarmed inside it.
describe("an armed job the section has no row for", () => {
  const ORPHAN = "com.addison.auto.older-cleanup";
  /** The row's own words. Frozen here byte-for-byte, as every user-facing string on
   * this surface is: the person reading it has no other source of truth about the job,
   * because there is no row and therefore no name, schedule or command to show. */
  const ORPHAN_NAME = "Running, but not saved here";
  const ORPHAN_WHY =
    "Your computer is running this on a schedule, but there's no saved copy of it " +
    "here — going back to an earlier restore point can leave one behind. Addison " +
    "can't show what it runs, only switch it off.";

  beforeEach(() => {
    vi.clearAllMocks();
  });

  async function renderSection(
    rows: Automation[],
    status: AutomationStatus | "unreachable" = { armed: [], supported: true },
    { developerSurface = true }: { developerSurface?: boolean } = {},
  ) {
    const { ipc } = await import("../ipc/client");
    (ipc.listAutomations as ReturnType<typeof vi.fn>).mockResolvedValue(rows);
    const statusMock = ipc.getAutomationStatus as ReturnType<typeof vi.fn>;
    if (status === "unreachable") statusMock.mockRejectedValue(new Error("engine went away"));
    else statusMock.mockResolvedValue(status);
    render(<Section developerSurface={developerSurface} onAsk={vi.fn()} />);
    // Every case here has SOMETHING to wait for: either the orphan row or the empty
    // line. Waiting on the section's own first paint keeps a passing assertion from
    // being one made before the fetches resolved.
    await waitFor(() =>
      expect(ipc.getAutomationStatus as ReturnType<typeof vi.fn>).toHaveBeenCalled(),
    );
    return ipc;
  }

  it("renders an armed label with no row as its own row, and says why in plain words", async () => {
    await renderSection([], { armed: [ORPHAN], supported: true });
    expect(await screen.findByText(ORPHAN_NAME)).toBeTruthy();
    // The LABEL is on screen, because it is the only fact left about this job.
    expect(screen.getByText(ORPHAN)).toBeTruthy();
    expect(screen.getByText(ORPHAN_WHY)).toBeTruthy();
    expect(screen.getByRole("button", { name: `Switch off ${ORPHAN}` })).toBeTruthy();
    // NOT a saved row, and it must never read as one: there is no command to show and
    // nothing here claims one.
    expect(screen.queryByText(COMMAND)).toBeNull();
  });

  it("does not duplicate a label that HAS a row", async () => {
    // The ordinary case — an armed automation the person can see — must be untouched
    // by the reconciliation. It renders once, as a saved row with its own controls.
    await renderSection([automation()], {
      armed: ["com.addison.auto.tidy-downloads"],
      supported: true,
    });
    await screen.findByText("Tidy up downloads");
    expect(screen.getByText(ARMED)).toBeTruthy();
    expect(screen.queryByText(ORPHAN_NAME)).toBeNull();
    expect(screen.getByRole("button", { name: "Disarm Tidy up downloads" })).toBeTruthy();
  });

  it("never renders a launchd job that isn't Addison's", async () => {
    // Somebody's own scheduled jobs are their business. A person must not open
    // Settings and find Addison listing — or offering to switch off — a job it did
    // not set up. Filtered to the labels Addison MINTS, the same shape the core
    // refuses anything outside of and the shell validates before it touches a file.
    await renderSection([], {
      armed: [
        "com.apple.something",
        "org.homebrew.autoupdate",
        "com.addison.autotidy", // the prefix run into the stem: not one of Addison's
        "com.addison.auto.Upper", // upper case: not a label Addison can mint
        "com.addison.auto.tidy.plist", // a dot after the prefix
      ],
      supported: true,
    });
    await screen.findByText(EMPTY);
    expect(screen.queryByText(ORPHAN_NAME)).toBeNull();
    expect(screen.queryByRole("button", { name: /^Switch off / })).toBeNull();
    const text = document.body.textContent ?? "";
    expect(text).not.toContain("com.apple.something");
    expect(text).not.toContain("org.homebrew.autoupdate");
  });

  it("takes two presses to switch off, and sends the label", async () => {
    // The section's own idiom, for a reason of its own: with no row there is nothing
    // to arm again, so an accidental press ENDS that job with no way back.
    const ipc = await renderSection([], { armed: [ORPHAN], supported: true });
    const button = await screen.findByRole("button", { name: `Switch off ${ORPHAN}` });
    fireEvent.click(button);
    expect(ipc.disarmOrphanAutomation).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: `Switch off ${ORPHAN}` }).textContent).toBe(
      "Really switch off?",
    );
    fireEvent.click(screen.getByRole("button", { name: `Switch off ${ORPHAN}` }));
    await waitFor(() => expect(ipc.disarmOrphanAutomation).toHaveBeenCalledWith(ORPHAN));
    expect(ipc.disarmOrphanAutomation).toHaveBeenCalledTimes(1);
    // Both answers are read again, because whether a label is an orphan is a fact
    // about the rows AND the OS together — and this ask is one the person just caused.
    await waitFor(() => expect(ipc.getAutomationStatus).toHaveBeenCalledTimes(2));
  });

  it("renders a refusal as the core's own sentence", async () => {
    const ipc = await renderSection([], { armed: [ORPHAN], supported: true });
    (ipc.disarmOrphanAutomation as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      error: "That automation is saved again, so switch it off from its own row in the list.",
    });
    const name = `Switch off ${ORPHAN}`;
    fireEvent.click(await screen.findByRole("button", { name }));
    fireEvent.click(screen.getByRole("button", { name }));
    expect(
      await screen.findByText(
        "That automation is saved again, so switch it off from its own row in the list.",
      ),
    ).toBeTruthy();
    expect(document.body.textContent ?? "").not.toContain("Traceback");
  });

  it("says something plain when the request cannot be sent at all", async () => {
    const ipc = await renderSection([], { armed: [ORPHAN], supported: true });
    (ipc.disarmOrphanAutomation as ReturnType<typeof vi.fn>).mockRejectedValueOnce(
      new Error("boom"),
    );
    const name = `Switch off ${ORPHAN}`;
    fireEvent.click(await screen.findByRole("button", { name }));
    fireEvent.click(screen.getByRole("button", { name }));
    expect(await screen.findByText("Addison couldn't switch that off just now.")).toBeTruthy();
    expect(document.body.textContent ?? "").not.toContain("boom");
  });

  it("invents no orphan when the SAVED ROWS could not be read", async () => {
    // The other half of "both answers have to be answers", and the one that bites
    // hardest: the hook keeps the last-known list when a fetch fails, which on a
    // FIRST load is the empty list it started with. Read as "nothing is saved", every
    // real automation this person has would render as "running, but not saved here" —
    // a screen telling somebody their own work has been lost, produced by one failed
    // request. (Found reviewing this change, before it landed.)
    const { ipc } = await import("../ipc/client");
    (ipc.listAutomations as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("engine"));
    (ipc.getAutomationStatus as ReturnType<typeof vi.fn>).mockResolvedValue({
      armed: [ORPHAN, "com.addison.auto.tidy-downloads"],
      supported: true,
    });
    render(<Section onAsk={vi.fn()} />);
    await waitFor(() => expect(ipc.getAutomationStatus).toHaveBeenCalled());
    await waitFor(() => expect(screen.queryByText(LOADING)).toBeNull());
    expect(screen.queryByText(ORPHAN_NAME)).toBeNull();
    expect(screen.queryByText(ORPHAN)).toBeNull();
    expect(screen.queryByRole("button", { name: /^Switch off / })).toBeNull();
  });

  it("invents no orphan when the OS could not be asked", async () => {
    // "Could not find out" is not "there is a job running". Guessing in EITHER
    // direction is wrong here, and this is the direction that would put a row on
    // screen naming a job that may not exist at all.
    await renderSection([], "unreachable");
    expect(await screen.findByText(STATUS_UNKNOWN)).toBeTruthy();
    expect(screen.queryByText(ORPHAN_NAME)).toBeNull();
  });

  it("invents no orphan from an ERROR beside an armed list", async () => {
    // The third outcome the core keeps apart: a sentence beside a list. The list is
    // not an answer, so nothing is reconciled against it.
    await renderSection([], {
      armed: [ORPHAN],
      supported: true,
      error: "Addison couldn't ask this computer just now.",
    });
    await screen.findByText("Addison couldn't ask this computer just now.");
    expect(screen.queryByText(ORPHAN_NAME)).toBeNull();
  });

  it("renders nothing where arming does not exist at all", async () => {
    // Off macOS `supported` is false and nothing is installed, so there is nothing to
    // reconcile — no orphan row, and no error either.
    await renderSection([], { armed: [ORPHAN], supported: false });
    expect(await screen.findByText(UNSUPPORTED)).toBeTruthy();
    expect(screen.queryByText(ORPHAN_NAME)).toBeNull();
    expect(screen.queryByRole("button", { name: /^Switch off / })).toBeNull();
  });

  it("shows the orphan and its Switch off in SIMPLE too", async () => {
    // A TIGHTENING IS NEVER PROFILE-GATED (Simple keeping Remove is the precedent),
    // and here it is the difference between having a way to stop a job and not having
    // one: every automation is armed from Developer, so a person who switches to
    // Simple and then restores an old point is exactly the person this strands.
    await renderSection([], { armed: [ORPHAN], supported: true }, { developerSurface: false });
    expect(await screen.findByText(ORPHAN_NAME)).toBeTruthy();
    expect(screen.getByText(ORPHAN_WHY)).toBeTruthy();
    expect(screen.getByRole("button", { name: `Switch off ${ORPHAN}` })).toBeTruthy();
  });
});
