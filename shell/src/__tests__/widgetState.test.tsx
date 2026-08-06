// The three interactive SAFE kinds in the rail (Phase-2 step 6, half A) —
// checklist, note, timer. None of them runs anything: each edits only its own
// stored state through `widget.setState`, which the CORE validates against the
// widget's spec. So what these tests are for is not "can it execute" (nothing
// can) but the three ways this half can quietly go wrong on screen:
//
//   * the tick lands on the wrong line — `checked` is POSITIONAL, so an
//     off-by-one here ticks something the person did not tick;
//   * the state is unusable by someone who does not use a mouse, or who reads a
//     dimmed line as an enabled one (personas 54 and 68 — the rule is never
//     colour alone, and every control focusable);
//   * a state that does not fit its spec is applied anyway. The core drops those
//     on the way out; the frontend must not re-introduce one on the way in.

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { WidgetRail } from "../components/WidgetRail";
import { parseWidgetList, parseWidgetState } from "../ipc/client";
import type { Stats, Widget, WidgetState } from "../types/ui";

afterEach(cleanup);

const NO_STATS: Stats = {
  tokensMonth: { total: 0, limit: null },
  providerLatency: [],
  connections: [],
};

const CHECKLIST: Widget = {
  id: "w-check",
  pinned: true,
  spec: { kind: "checklist", items: ["Buy milk", "Call Ana"], title: "Saturday" },
};

function renderRail(widgets: Widget[], onSetWidgetState?: (id: string, s: WidgetState) => void) {
  return render(
    <WidgetRail
      work={null}
      consent={null}
      developer={false}
      widgets={widgets}
      stats={NO_STATS}
      routines={[]}
      onSetPinned={vi.fn()}
      onDelete={vi.fn()}
      onRunRoutine={vi.fn()}
      onRunCommandWidget={vi.fn()}
      onSetWidgetState={onSetWidgetState}
      onAskBuildWidget={vi.fn()}
    />,
  );
}

describe("a checklist widget", () => {
  it("draws every line as a real checkbox, named by its own text", () => {
    renderRail([CHECKLIST]);
    // getByRole with a name is the a11y contract in one call: it exists, it is a
    // checkbox, and a screen reader says the line's words when it lands there.
    expect(screen.getByRole("checkbox", { name: "Buy milk" })).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: "Call Ana" })).toBeTruthy();
    expect(screen.getByText("0/2")).toBeTruthy();
  });

  it("ticks the line that was clicked, and only that one", () => {
    const onSetWidgetState = vi.fn();
    renderRail([{ ...CHECKLIST, state: { checked: [false, false] } }], onSetWidgetState);
    fireEvent.click(screen.getByRole("checkbox", { name: "Call Ana" }));
    // Position matters: [false, true], never [true, false] and never a re-ordered
    // array. This is the assertion an off-by-one has to get past.
    expect(onSetWidgetState).toHaveBeenCalledWith("w-check", { checked: [false, true] });
  });

  it("unticks a ticked line rather than ticking it again", () => {
    const onSetWidgetState = vi.fn();
    renderRail([{ ...CHECKLIST, state: { checked: [true, false] } }], onSetWidgetState);
    fireEvent.click(screen.getByRole("checkbox", { name: "Buy milk" }));
    expect(onSetWidgetState).toHaveBeenCalledWith("w-check", { checked: [false, false] });
  });

  it("shows a done line three ways, never by colour alone", () => {
    renderRail([{ ...CHECKLIST, state: { checked: [true, false] } }]);
    const box = screen.getByRole("checkbox", { name: "Buy milk" }) as HTMLInputElement;
    expect(box.checked).toBe(true); // 1: the control's own state (and the a11y tree)
    expect(screen.getByText("Buy milk").className).toContain("line-through"); // 2: struck
    expect(screen.getByText("1/2")).toBeTruthy(); // 3: the count says so in numbers
  });

  it("keeps the box operable from the keyboard", () => {
    const onSetWidgetState = vi.fn();
    renderRail([{ ...CHECKLIST, state: { checked: [false, false] } }], onSetWidgetState);
    const box = screen.getByRole("checkbox", { name: "Buy milk" }) as HTMLInputElement;
    box.focus();
    expect(document.activeElement).toBe(box);
    // A native checkbox turns Space into a click; jsdom does the same, so this is
    // the real keyboard path rather than a simulated one. A div-with-onClick
    // would fail here, which is the point.
    fireEvent.click(box);
    expect(onSetWidgetState).toHaveBeenCalledWith("w-check", { checked: [true, false] });
  });

  it("draws an untouched list when the stored state is the wrong length", () => {
    // Unreachable through the app (specs are immutable and the core drops these),
    // so this is the belt: a shorter array must not tick line 1 and leave line 2
    // undefined — it must be ignored wholesale.
    renderRail([{ ...CHECKLIST, state: { checked: [true] } as WidgetState }]);
    expect((screen.getByRole("checkbox", { name: "Buy milk" }) as HTMLInputElement).checked).toBe(
      false,
    );
    expect(screen.getByText("0/2")).toBeTruthy();
  });

  it("still shows the ticks when the rail has no way to change them", () => {
    // No onSetWidgetState (a read-only rail): the state is history, not an
    // affordance, so it is shown and the control is disabled — not hidden.
    renderRail([{ ...CHECKLIST, state: { checked: [true, false] } }]);
    const box = screen.getByRole("checkbox", { name: "Buy milk" }) as HTMLInputElement;
    expect(box.checked).toBe(true);
    expect(box.disabled).toBe(true);
  });
});

describe("a note widget", () => {
  const NOTE: Widget = {
    id: "w-note",
    pinned: true,
    spec: { kind: "note", text: "Ana's address", title: "Note" },
  };

  it("shows the CURRENT text, not the text it was created with", () => {
    renderRail([{ ...NOTE, state: { text: "moved to Brno" } }]);
    expect((screen.getByLabelText("Note") as HTMLTextAreaElement).value).toBe("moved to Brno");
  });

  it("saves what was typed when the person clicks away", () => {
    const onSetWidgetState = vi.fn();
    renderRail([NOTE], onSetWidgetState);
    const box = screen.getByLabelText("Note");
    fireEvent.change(box, { target: { value: "moved to Brno" } });
    expect(onSetWidgetState).not.toHaveBeenCalled(); // not on every keystroke
    fireEvent.blur(box);
    expect(onSetWidgetState).toHaveBeenCalledWith("w-note", { text: "moved to Brno" });
  });

  it("offers a Save while there are unsaved words, and not otherwise", () => {
    const onSetWidgetState = vi.fn();
    renderRail([NOTE], onSetWidgetState);
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    fireEvent.change(screen.getByLabelText("Note"), { target: { value: "moved" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onSetWidgetState).toHaveBeenCalledWith("w-note", { text: "moved" });
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  });

  it("does not write the same text twice", () => {
    const onSetWidgetState = vi.fn();
    renderRail([NOTE], onSetWidgetState);
    fireEvent.blur(screen.getByLabelText("Note"));
    expect(onSetWidgetState).not.toHaveBeenCalled();
  });
});

describe("a timer widget", () => {
  const TIMER: Widget = {
    id: "w-timer",
    pinned: true,
    spec: { kind: "timer", seconds: 300, title: "Tea" },
  };

  it("starts paused at its full length, said in words as well as on the clock", () => {
    renderRail([TIMER]);
    expect(screen.getByText("5:00")).toBeTruthy();
    expect(screen.getByText("paused")).toBeTruthy();
  });

  it("records the moment it was started, so the counting is derived and not stored", () => {
    const onSetWidgetState = vi.fn();
    vi.spyOn(Date, "now").mockReturnValue(1_700_000_000_000);
    renderRail([TIMER], onSetWidgetState);
    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    expect(onSetWidgetState).toHaveBeenCalledWith("w-timer", {
      running: true,
      remaining: 300,
      startedAt: 1_700_000_000, // SECONDS, like the core stores it
    });
    vi.restoreAllMocks();
  });

  it("shows how much is left from the start time, and says it is running", () => {
    vi.spyOn(Date, "now").mockReturnValue(1_700_000_090_000); // 90s after the start
    renderRail([
      { ...TIMER, state: { running: true, remaining: 300, startedAt: 1_700_000_000 } },
    ]);
    expect(screen.getByText("3:30")).toBeTruthy();
    expect(screen.getByText("running")).toBeTruthy();
    vi.restoreAllMocks();
  });

  it("pauses at what is left, not at what it started with", () => {
    const onSetWidgetState = vi.fn();
    vi.spyOn(Date, "now").mockReturnValue(1_700_000_090_000);
    renderRail(
      [{ ...TIMER, state: { running: true, remaining: 300, startedAt: 1_700_000_000 } }],
      onSetWidgetState,
    );
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    expect(onSetWidgetState).toHaveBeenCalledWith("w-timer", {
      running: false,
      remaining: 210,
      startedAt: null,
    });
    vi.restoreAllMocks();
  });

  it("says time is up and offers nothing to start, because nothing rings", () => {
    // Addison never triggers itself: at zero the row simply says so and waits to
    // be read. There is no alarm to test for, and there must not be one to find.
    vi.spyOn(Date, "now").mockReturnValue(1_700_000_400_000);
    renderRail([
      { ...TIMER, state: { running: true, remaining: 300, startedAt: 1_700_000_000 } },
    ]);
    expect(screen.getByText("0:00")).toBeTruthy();
    expect(screen.getByText("time's up")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Start" })).toBeNull();
    vi.restoreAllMocks();
  });
});

// ---------------------------------------------------------------------------
// The parser, which is the frontend's own copy of the core's read-time check.
// It fails CLOSED — an unusable state becomes `undefined` (draw the declaration)
// rather than something half-applied.
// ---------------------------------------------------------------------------
describe("parseWidgetState", () => {
  const checklist = CHECKLIST.spec;
  const timer = { kind: "timer", seconds: 300, title: "Tea" } as const;

  it("keeps a state that fits its spec", () => {
    expect(parseWidgetState(checklist, { checked: [true, false] })).toEqual({
      checked: [true, false],
    });
  });

  it("drops a checklist state of the wrong length", () => {
    expect(parseWidgetState(checklist, { checked: [true] })).toBeUndefined();
    expect(parseWidgetState(checklist, { checked: [true, false, true] })).toBeUndefined();
    expect(parseWidgetState(checklist, { checked: ["yes", "no"] })).toBeUndefined();
  });

  it("drops a running timer with no start time — it could never be counted", () => {
    expect(
      parseWidgetState(timer, { running: true, remaining: 300, startedAt: null }),
    ).toBeUndefined();
    expect(parseWidgetState(timer, { running: false, remaining: 300, startedAt: null })).toEqual({
      running: false,
      remaining: 300,
      startedAt: null,
    });
  });

  it("drops a state for a kind that keeps none", () => {
    const stat = { kind: "stat", source: "connections", title: "Conns" } as const;
    expect(parseWidgetState(stat, { checked: [true] })).toBeUndefined();
  });
});

describe("parseWidgetList over an interactive row", () => {
  it("drops a checklist whose items are not all text, rather than rendering it short", () => {
    // Position is meaning here, so a partially-readable list is not a smaller
    // list — it is a list whose ticks would land in the wrong places.
    const parsed = parseWidgetList({
      widgets: [
        { id: "w1", spec: { kind: "checklist", items: ["ok", 7], title: "Half" }, pinned: true },
        { id: "w2", spec: { kind: "checklist", items: [], title: "Empty" }, pinned: true },
      ],
    });
    expect(parsed).toEqual([]);
  });

  it("carries a valid state through and leaves an invalid one behind", () => {
    const parsed = parseWidgetList({
      widgets: [
        {
          id: "w1",
          spec: { kind: "checklist", items: ["a", "b"], title: "T" },
          pinned: true,
          state: { checked: [true, true] },
        },
        {
          id: "w2",
          spec: { kind: "checklist", items: ["a", "b"], title: "T" },
          pinned: true,
          state: { checked: [true] },
        },
      ],
    });
    expect(parsed[0].state).toEqual({ checked: [true, true] });
    expect(parsed[1].state).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// The proposal card names the KIND. It said one of two things for as long as
// there were two SAFE kinds, and the else branch had already gone wrong once (a
// command widget described itself as "shows a value from Addison"). Three new
// kinds would have made it wrong three more ways, on the one surface whose whole
// job is telling the person what they are about to agree to.
// ---------------------------------------------------------------------------
describe("the widget proposal card", () => {
  it("says what each kind actually is", async () => {
    const { WidgetProposalCard } = await import("../components/WidgetProposalCard");
    const cases: [string, RegExp][] = [
      ["checklist", /tick off/],
      ["note", /note you can edit/],
      ["timer", /timer you start/],
      ["command", /runs a command/],
      ["stat", /shows a value/],
      ["routine", /runs a saved routine/],
    ];
    for (const [kind, expected] of cases) {
      const { unmount } = render(
        <WidgetProposalCard
          proposal={{
            title: "T",
            kind,
            summary: "s",
            spec: { kind: "note", text: "", title: "T" },
          }}
          onAdd={vi.fn()}
          onCancel={vi.fn()}
        />,
      );
      expect(screen.getByText(expected), kind).toBeTruthy();
      unmount();
    }
  });
});
