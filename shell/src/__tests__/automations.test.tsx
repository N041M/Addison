// Automations — the work Addison writes down for THIS COMPUTER to run (Phase-2
// step 8, phases 2 and 3: authoring, then arming). Five parts:
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
//   (d) The page-level gate: the section renders on the Developer/Custom surfaces
//       only (keyed off the active profile, never the mode). Simple never sees it,
//       which is phase 3's honest position — an automation's payload is a shell
//       command, and phase 4 replaces the gate with a listed-but-disabled treatment.
//   (e) ARMED-NESS COMES FROM THE OPERATING SYSTEM (plan §5.6). Asked once when the
//       section loads, never stored and never polled; a row that was never answered
//       for says NOTHING about armed-ness rather than the comfortable half. Arm is
//       offered only where arming exists, Disarm only for what the OS says is armed,
//       and neither one calls anything: they write a sentence into the composer,
//       because the ceremony belongs on the card and a settings button that installs
//       a recurring job is the reflex the ceremony exists to break.
//
// The generated fixture (fixtures/automation.list.json, produced by
// tests/ipc_fixtures.py from the real handler) is consumed by
// parsers.fixtures.test.ts — that is where this parser meets a payload nobody on
// this side wrote.

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";
import { parseAutomations, parseAutomationStatus } from "../ipc/client";
import { AutomationsSection, SettingsPage } from "../components/SettingsPage";
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
    render(<AutomationsSection connected={true} onAsk={onAsk} />);
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
    render(<AutomationsSection connected={true} />);
    expect(screen.getByText(LOADING)).toBeTruthy();
    expect(screen.queryByText(EMPTY)).toBeNull();
  });

  it("asks nothing at all while the engine is down, and says so", async () => {
    const { ipc } = await import("../ipc/client");
    render(<AutomationsSection connected={false} />);
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
    const ipc = await renderSection([automation()]);
    expect(ipc.listAutomations).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Remove Tidy up downloads" }));
    fireEvent.click(screen.getByRole("button", { name: "Remove Tidy up downloads" }));
    await waitFor(() => expect(ipc.listAutomations).toHaveBeenCalledTimes(2));
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
    render(<AutomationsSection connected={true} onAsk={onAsk} />);
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
    render(<AutomationsSection connected={true} />);
    await screen.findByText(TIDY.name);
    expect(screen.getByText(NOT_ARMED)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Arm / })).toBeNull();
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

function renderSettings(profile: ProfileState) {
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
      profile={profile}
      onSetProfile={noop}
      diagnostics={[]}
      onClearDiagnostics={noop}
      theme="light"
      onSetTheme={noop}
    />,
  );
}

describe("the Automations section gate", () => {
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

  it("does NOT render on the Simple surface", () => {
    // Phase 2's honest position: an automation's payload is a shell command, which
    // SAFE has no place for (plan §5.3), and the tool that writes one is dev-only
    // and refused at dispatch outside OPEN whatever this page draws. Phase 4 turns
    // this into a listed-but-disabled treatment — the artifact rule — which is a
    // change to what Simple SHOWS, never to what it may do.
    renderSettings({ ...PROFILE, activeProfile: "simple", mode: "safe" });
    expect(screen.queryByText(SECTION_TITLE)).toBeNull();
    expect(document.body.textContent ?? "").not.toContain(NOT_ARMED);
  });
});
