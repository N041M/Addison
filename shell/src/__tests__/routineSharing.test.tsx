// Routine sharing, seen from the surface a person actually uses.
//
// The library is the whole of the UI for it: "Share" on each row writes a routine
// out, one row at the end reads one back in, and the card in between is where a
// person decides whether to add a plan somebody else wrote. What is tested here is
// the half a frontend can get wrong on its own, which is the SAYING.
//
// The three assurances are the reason this file exists. They are the honest
// description of a stranger's file: that nothing is pre-approved, that Addison has
// not checked what the routine is for, and that it can be removed and there is a
// restore point. They are the only thing standing between "somebody sent me a
// file" and "I pressed a button". A card that dropped one, softened one, or showed
// its own friendlier summary instead would be the exact failure, and it would be
// invisible from the Python side, where all three tests pass on a payload nobody
// renders. So they are asserted WORD FOR WORD, against the fixture the core itself
// generated (tests/ipc_fixtures.py), so a change to either side fails here.
//
// The rest is the same rule applied to the smaller sentences: the screening note
// appears when the core sent one and never otherwise, the needs-Developer notice
// says the row will land switched off, and an export refusal is shown as the core
// worded it, beside the routine it is about.

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor, act } from "@testing-library/react";
import { RoutineLibrary } from "../components/RoutineLibrary";
import { ipc } from "../ipc/client";
import importPreviewFixture from "./fixtures/routine.importPreview.json";

afterEach(cleanup);

vi.mock("../ipc/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../ipc/client")>();
  return {
    ...actual,
    isEngineConnected: () => true,
    subscribeCoreState: () => () => {},
    ipc: {
      ...actual.ipc,
      listRoutines: vi.fn(async () => ({ routines: [] })),
      runRoutine: vi.fn(async () => ({ ok: true, detail: "Done." })),
      deleteRoutine: vi.fn(async () => ({ ok: true })),
      exportRoutine: vi.fn(async () => ({ ok: true, path: "/tmp/morning-news.json" })),
      previewRoutineImport: vi.fn(async () => importPreviewFixture),
      confirmRoutineImport: vi.fn(async () => ({ ok: true, routineId: "new-r" })),
    },
  };
});

// The three sentences, transcribed from the core (agent_core/rpc/routines.py,
// `_IMPORT_ASSURANCES`) rather than read out of the payload, because a test that compares
// the payload to itself proves only that the payload exists.
const ASSURANCES = [
  "This routine can't do anything you haven't approved. Addison still asks before each action, exactly as it does now.",
  "Addison hasn't checked what this routine is for. Only add it if you trust the person who sent it.",
  "You can delete it at any time, and Addison saves a restore point before adding it.",
];

const ONE_ROUTINE = {
  routines: [
    {
      id: "r1",
      name: "Morning news",
      description: "",
      runCount: 0,
      lastRunAt: null,
      variables: [],
      createdInMode: "safe",
    },
  ],
};

// A routine that arrived from a shared file and needs abilities Simple does not
// have. The core lists it exactly like any other waiting artifact: a marker and
// the sentence it refuses the run with, which is the machinery this feature was
// deliberately built on top of rather than beside.
const IMPORTED_WAITING_ROUTINE = {
  routines: [
    {
      id: "imported-r",
      name: "Rebuild the site",
      description: "",
      runCount: 0,
      lastRunAt: null,
      variables: [],
      createdInMode: "safe",
      unavailable: {
        reason: "developer_abilities",
        message: "That routine uses developer abilities, so it's waiting in Developer profile.",
      },
    },
  ],
};

async function openTheCard() {
  render(<RoutineLibrary />);
  const choose = await screen.findByRole("button", {
    name: "Add a routine someone shared with you",
  });
  await act(async () => {
    fireEvent.click(choose);
  });
}

describe("the card that asks about a routine somebody else wrote", () => {
  beforeEach(() => {
    vi.mocked(ipc.listRoutines).mockResolvedValue(ONE_ROUTINE as never);
    vi.mocked(ipc.previewRoutineImport).mockResolvedValue(importPreviewFixture as never);
    vi.mocked(ipc.confirmRoutineImport).mockClear();
  });

  it("shows all three assurances word for word", async () => {
    await openTheCard();

    for (const sentence of ASSURANCES) {
      expect(screen.getByText(sentence)).toBeTruthy();
    }
    // ...and they are the core's own, not a copy that has drifted from it.
    expect(importPreviewFixture.assurances).toEqual(ASSURANCES);
  });

  it("shows the name, the numbered steps and what it will ask for", async () => {
    await openTheCard();

    expect(screen.getByLabelText("Add this shared routine?")).toBeTruthy();
    expect(screen.getByText("Looks up one topic each morning.")).toBeTruthy();
    expect(screen.getByText("1. Search the web")).toBeTruthy();
    expect(screen.getByText(/What should I look up\?/)).toBeTruthy();
  });

  it("adds nothing until Add it is pressed, and asks the core to add it when it is", async () => {
    await openTheCard();

    // Reading the file saved nothing: the confirm has not been called.
    expect(vi.mocked(ipc.confirmRoutineImport)).not.toHaveBeenCalled();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Add it" }));
    });
    expect(vi.mocked(ipc.confirmRoutineImport)).toHaveBeenCalledTimes(1);
  });

  it("says no plainly, and never asks the core, when the person declines", async () => {
    await openTheCard();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Don't add it" }));
    });
    expect(vi.mocked(ipc.confirmRoutineImport)).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Add this shared routine?")).toBeNull();
  });

  it("renders the core's own answer when the file is already used up", async () => {
    // The double-press case. The core has already spent what it was holding and
    // answers in a plain sentence; the surface's job is to show that sentence and
    // not to invent an explanation of its own.
    const spent = "There's no shared routine waiting to be added. Choose the file again.";
    vi.mocked(ipc.confirmRoutineImport).mockResolvedValue({ ok: false, error: spent } as never);
    await openTheCard();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Add it" }));
    });
    expect(screen.getByText(spent)).toBeTruthy();
  });
});

describe("the notes that ride on a preview", () => {
  beforeEach(() => {
    vi.mocked(ipc.listRoutines).mockResolvedValue(ONE_ROUTINE as never);
  });

  const SCREENING_NOTE =
    "Some of the wording in this file is written as if it were an instruction to " +
    "Addison. Addison will treat it as text.";

  it("shows the screening note when the core sent one", async () => {
    vi.mocked(ipc.previewRoutineImport).mockResolvedValue({
      ...importPreviewFixture,
      screeningNote: SCREENING_NOTE,
    } as never);
    await openTheCard();

    expect(screen.getByText(SCREENING_NOTE)).toBeTruthy();
    // Flagged wording is not a refusal: the person is still the one who decides.
    expect(screen.getByRole("button", { name: "Add it" })).toBeTruthy();
  });

  it("shows no such note when the core sent none", async () => {
    vi.mocked(ipc.previewRoutineImport).mockResolvedValue(importPreviewFixture as never);
    await openTheCard();

    expect(screen.queryByText(SCREENING_NOTE)).toBeNull();
    expect(screen.queryByText(/written as if it were an instruction/)).toBeNull();
  });

  it("says when the routine will land switched off", async () => {
    vi.mocked(ipc.previewRoutineImport).mockResolvedValue({
      ...importPreviewFixture,
      needsDeveloper: true,
    } as never);
    await openTheCard();

    expect(
      screen.getByText(
        /This routine needs the Developer profile to run\. It will be listed, and switched off, until then\./,
      ),
    ).toBeTruthy();
  });
});

describe("sharing a routine out", () => {
  beforeEach(() => {
    vi.mocked(ipc.listRoutines).mockResolvedValue(ONE_ROUTINE as never);
    vi.mocked(ipc.exportRoutine).mockClear();
  });

  it("offers Share on the row and asks the core for that routine", async () => {
    render(<RoutineLibrary />);
    const share = await screen.findByRole("button", { name: "Share Morning news" });
    await act(async () => {
      fireEvent.click(share);
    });
    expect(vi.mocked(ipc.exportRoutine)).toHaveBeenCalledWith("r1");
  });

  it("shows a refusal beside the row, in the words the core refused with", async () => {
    const refusal =
      'This routine cannot be shared because the step "step_1" runs a command on your ' +
      "computer. Shared routines can only use Addison's own actions, never commands.";
    vi.mocked(ipc.exportRoutine).mockResolvedValue({ ok: false, error: refusal } as never);

    render(<RoutineLibrary />);
    const share = await screen.findByRole("button", { name: "Share Morning news" });
    await act(async () => {
      fireEvent.click(share);
    });

    await waitFor(() => expect(screen.getByText(refusal)).toBeTruthy());
  });
});

describe("an imported routine the active profile cannot run", () => {
  it("is listed disabled through the existing waiting rendering", async () => {
    vi.mocked(ipc.listRoutines).mockResolvedValue(IMPORTED_WAITING_ROUTINE as never);
    render(<RoutineLibrary />);

    // Listed at all, annotated, and saying why: the same three things any waiting
    // artifact does. Import deliberately reuses this rather than adding a second
    // way for a row to be switched off.
    await waitFor(() => expect(screen.getByText("Rebuild the site")).toBeTruthy());
    expect(screen.getByText("Waiting")).toBeTruthy();
    expect(
      screen.getByText(
        "That routine uses developer abilities, so it's waiting in Developer profile.",
      ),
    ).toBeTruthy();
    // No Run, because the core could only refuse it...
    expect(screen.queryByRole("button", { name: "Run Rebuild the site" })).toBeNull();
    // ...but Share is still there: passing the plan on is not running it.
    expect(screen.getByRole("button", { name: "Share Rebuild the site" })).toBeTruthy();
  });
});
