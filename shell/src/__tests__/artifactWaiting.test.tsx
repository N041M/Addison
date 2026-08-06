// Artifacts made with developer abilities, seen from the Simple profile.
//
// They used to be FILTERED OUT of `routine.list` / `widget.list`, so switching
// Developer → Simple emptied the library and the rail and the person's own work
// appeared to be gone. The core now lists them carrying an `unavailable`
// {reason, message} marker (owner decision 2026-08-06), and these two surfaces
// have to render that honestly:
//
//   * the reason must be ON SCREEN as a sentence — a row that is inert with no
//     explanation is the same "where did my stuff go?" bug one step later;
//   * the row must not offer a control that could only come back refused; and
//   * clicking it must not fire a run. The core refuses these calls on its own
//     (that is the enforcement, and it is tested in tests/test_policy_modes.py) —
//     what is tested here is that this frontend never even asks.
//
// The positive control in each half matters as much: an ordinary Simple artifact
// beside the waiting one must keep its Run and still work, or "disabled" would
// have quietly become "everything is disabled".

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor, act } from "@testing-library/react";
import { RoutineLibrary } from "../components/RoutineLibrary";
import { WidgetRail } from "../components/WidgetRail";
import { normalizeUnavailable } from "../lib/parse";
import { normalizeRailRoutines } from "../hooks/useWidgets";
import { ipc } from "../ipc/client";
import type { Stats, Widget } from "../types/ui";

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
    },
  };
});

const WAITING_ROUTINE_SENTENCE =
  "That routine uses developer abilities, so it's waiting in Developer profile.";
const WAITING_WIDGET_SENTENCE =
  "That widget uses developer abilities, so it's waiting in Developer profile.";

// The wire shape the core sends in Simple: one dev-made routine (marked) and one
// ordinary one (no marker at all).
const ROUTINE_ROWS = {
  routines: [
    {
      id: "dev-r",
      name: "Rebuild the site",
      description: "",
      runCount: 3,
      lastRunAt: null,
      variables: [],
      createdInMode: "open",
      unavailable: { reason: "developer_abilities", message: WAITING_ROUTINE_SENTENCE },
    },
    {
      id: "safe-r",
      name: "Morning brief",
      description: "",
      runCount: 0,
      lastRunAt: null,
      variables: [],
      createdInMode: "safe",
    },
  ],
};

// ---------------------------------------------------------------------------
// The Routines library
// ---------------------------------------------------------------------------
describe("a routine that is waiting for the Developer profile", () => {
  beforeEach(() => {
    vi.mocked(ipc.listRoutines).mockClear();
    vi.mocked(ipc.runRoutine).mockClear();
    vi.mocked(ipc.listRoutines).mockResolvedValue(ROUTINE_ROWS as never);
  });

  it("is listed, says why in plain language, and offers no Run", async () => {
    render(<RoutineLibrary />);

    // Listed at all — the whole point of the change.
    await waitFor(() => expect(screen.getByText("Rebuild the site")).toBeTruthy());
    // ...saying why, in the same sentence the core refuses the run with.
    expect(screen.getByText(WAITING_ROUTINE_SENTENCE)).toBeTruthy();
    // ...and annotated, so "disabled" is never carried by colour alone.
    expect(screen.getByText("Waiting")).toBeTruthy();

    // Exactly one Run on screen: the ordinary routine's. The waiting row has none.
    const runs = screen.queryAllByRole("button", { name: /^Run / });
    expect(runs).toHaveLength(1);
    expect(runs[0].getAttribute("aria-label")).toBe("Run Morning brief");
    expect(screen.queryByRole("button", { name: "Run Rebuild the site" })).toBeNull();
  });

  it("never asks the core to run it, however the row is clicked", async () => {
    const { container } = render(<RoutineLibrary />);
    await waitFor(() => expect(screen.getByText("Rebuild the site")).toBeTruthy());

    // The row itself, its name, and its reason line — nothing here is a control.
    const row = screen.getByText("Rebuild the site").closest("div");
    await act(async () => {
      fireEvent.click(row!);
      fireEvent.click(screen.getByText("Rebuild the site"));
      fireEvent.click(screen.getByText(WAITING_ROUTINE_SENTENCE));
      // Belt: every button rendered inside the waiting row (Remove is the only
      // one, and it is not a run).
      container
        .querySelectorAll("button")
        .forEach((b) => b.getAttribute("aria-label")?.includes("Rebuild") && fireEvent.click(b));
    });

    expect(ipc.runRoutine).not.toHaveBeenCalled();
  });

  it("still runs the ordinary routine beside it", async () => {
    render(<RoutineLibrary />);
    await waitFor(() => expect(screen.getByText("Morning brief")).toBeTruthy());

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Run Morning brief" }));
    });

    expect(vi.mocked(ipc.runRoutine).mock.calls.map((c) => c[0])).toEqual(["safe-r"]);
  });
});

// ---------------------------------------------------------------------------
// The widget rail
// ---------------------------------------------------------------------------
const STATS: Stats = {
  tokensMonth: { total: 0, limit: null },
  providerLatency: [],
  connections: [
    { id: "ollama", label: "Ollama · this computer", status: "unreachable", detail: "not running" },
  ],
};

function renderRail(widgets: Widget[], routines: React.ComponentProps<typeof WidgetRail>["routines"] = []) {
  const onRunRoutine = vi.fn(async () => ({ ok: true, detail: "Done." }));
  const onRunCommandWidget = vi.fn(async () => ({ ok: true }));
  render(
    <WidgetRail
      work={null}
      consent={null}
      developer={false}
      widgets={widgets}
      stats={STATS}
      routines={routines}
      onSetPinned={vi.fn()}
      onDelete={vi.fn()}
      onRunRoutine={onRunRoutine}
      onRunCommandWidget={onRunCommandWidget}
      onAskBuildWidget={vi.fn()}
    />,
  );
  return { onRunRoutine, onRunCommandWidget };
}

const WAITING_COMMAND_WIDGET: Widget = {
  id: "w-dev",
  pinned: true,
  spec: { kind: "command", command: "rm -rf ~/Documents", title: "Tidy up" },
  createdInMode: "open",
  unavailable: { reason: "developer_abilities", message: WAITING_WIDGET_SENTENCE },
};

const ORDINARY_ROUTINE_WIDGET: Widget = {
  id: "w-safe",
  pinned: true,
  spec: { kind: "routine", routineId: "safe-r", title: "Morning brief" },
  createdInMode: "safe",
};

describe("a widget that is waiting for the Developer profile", () => {
  it("is shown with its reason, without a Run and without its command", () => {
    const { onRunCommandWidget } = renderRail([WAITING_COMMAND_WIDGET]);

    expect(screen.getByText("Tidy up")).toBeTruthy();
    expect(screen.getByText(WAITING_WIDGET_SENTENCE)).toBeTruthy();
    expect(screen.getByText("waiting")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
    // A Simple rail never prints a shell command, disabled row or not.
    expect(document.body.textContent).not.toContain("rm -rf");

    fireEvent.click(screen.getByText("Tidy up"));
    fireEvent.click(screen.getByText(WAITING_WIDGET_SENTENCE));
    expect(onRunCommandWidget).not.toHaveBeenCalled();
  });

  it("reads as waiting when the ROUTINE behind a routine widget is the dev one", () => {
    // The widget itself was made in Simple, so it carries no marker — but the
    // routine it would run cannot be used, and pressing Run could only be
    // refused. Same disappointment, so the same row.
    const { onRunRoutine } = renderRail(
      [{ ...ORDINARY_ROUTINE_WIDGET, spec: { kind: "routine", routineId: "dev-r", title: "Rebuild" } }],
      [
        {
          id: "dev-r",
          name: "Rebuild the site",
          variables: [],
          createdInMode: "open",
          unavailable: { reason: "developer_abilities", message: WAITING_ROUTINE_SENTENCE },
        },
      ],
    );

    expect(screen.getByText(WAITING_ROUTINE_SENTENCE)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Run" })).toBeNull();
    fireEvent.click(screen.getByText("Rebuild"));
    expect(onRunRoutine).not.toHaveBeenCalled();
  });

  it("does not stand the ambient rows down for a source it never draws", () => {
    // A stat widget made in Developer is now IN the Simple rail (as a waiting
    // row), and the rail's one rule is "a core-computed source is drawn by the
    // first thing on screen that claims it". A row that shows a reason instead of
    // numbers must therefore claim nothing — otherwise it silences the ambient
    // connection block on behalf of a row that shows no connections, and the
    // person's Ollama/Anthropic rows vanish along with the widget's numbers.
    renderRail([
      {
        id: "w-dev-stat",
        pinned: true,
        spec: { kind: "stat", source: "connections", title: "Connections" },
        createdInMode: "open",
        unavailable: { reason: "developer_abilities", message: WAITING_WIDGET_SENTENCE },
      },
    ]);

    expect(screen.getByText(WAITING_WIDGET_SENTENCE)).toBeTruthy();
    expect(screen.getAllByText("Ollama · this computer")).toHaveLength(1);
  });

  it("leaves an ordinary widget beside it fully usable", async () => {
    const { onRunRoutine } = renderRail(
      [WAITING_COMMAND_WIDGET, ORDINARY_ROUTINE_WIDGET],
      [{ id: "safe-r", name: "Morning brief", variables: [], createdInMode: "safe" }],
    );

    const run = screen.getByRole("button", { name: "Run" });
    await act(async () => {
      fireEvent.click(run);
    });
    expect(onRunRoutine).toHaveBeenCalledWith("safe-r", {});
  });
});

// ---------------------------------------------------------------------------
// The rail's own copy of the routine list
// ---------------------------------------------------------------------------
describe("normalizeRailRoutines", () => {
  it("carries the marker through to the rail, and leaves usable routines alone", () => {
    const parsed = normalizeRailRoutines(ROUTINE_ROWS);
    expect(parsed.map((r) => r.id)).toEqual(["dev-r", "safe-r"]);
    expect(parsed[0].unavailable).toEqual({
      reason: "developer_abilities",
      message: WAITING_ROUTINE_SENTENCE,
    });
    expect(parsed[1].unavailable).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// The parser underneath both
// ---------------------------------------------------------------------------
describe("normalizeUnavailable", () => {
  it("keeps a reason slug it has never heard of", () => {
    // The vocabulary is the core's, and it is open by design (a tool that becomes
    // dev-only in an update is the next slug). A frontend that treated an unknown
    // cause as "no problem" would re-enable a control the core will refuse.
    expect(normalizeUnavailable({ reason: "tool_withdrawn", message: "Not right now." })).toEqual({
      reason: "tool_withdrawn",
      message: "Not right now.",
    });
  });

  it("ignores a marker with nothing to show the person", () => {
    // Disabling a row while saying nothing is the bug this feature exists to fix.
    expect(normalizeUnavailable({ reason: "developer_abilities" })).toBeUndefined();
    expect(normalizeUnavailable({ reason: "developer_abilities", message: "  " })).toBeUndefined();
    expect(normalizeUnavailable(undefined)).toBeUndefined();
    expect(normalizeUnavailable("waiting")).toBeUndefined();
  });

  it("still marks a row whose reason slug is missing", () => {
    // The sentence is what the person reads; a payload that carries one is
    // unavailable whether or not the machine-readable half arrived.
    expect(normalizeUnavailable({ message: WAITING_ROUTINE_SENTENCE })).toEqual({
      reason: "",
      message: WAITING_ROUTINE_SENTENCE,
    });
  });
});
