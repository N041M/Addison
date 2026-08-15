// A routine's run, watched and answered (owner decision 2026-08-12, closing the
// QA artifact's §06 open question).
//
// Running a routine from Settings used to report ONE terminal row — "Done — every
// step finished" — and nothing else: not which steps ran, not that one was waiting
// on a permission card, and not the thing the routine actually produced (a "Quick
// Sums" run computed 6016 and 6016 appeared nowhere).
//
// So four properties, each of which was false before:
//
//   * the steps appear AS THEY ARRIVE, live, rather than in a report afterwards;
//   * the answer is shown as readable text when the run completes;
//   * a step that failed says which step and why, in a plain sentence; and
//   * a step waiting on a permission card reads as waiting rather than as work.

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor, act } from "@testing-library/react";
import { RoutineLibrary } from "../components/RoutineLibrary";
import { ipc } from "../ipc/client";
import { Method } from "../types/protocol";

afterEach(cleanup);

// Notification handlers, kept per method so a test can push a frame the way the
// core would. `subscribe` is the real function's contract: register, return an
// unsubscribe.
const handlers = new Map<string, Set<(params: Record<string, unknown>) => void>>();

function emit(method: string, params: Record<string, unknown>) {
  act(() => {
    for (const handler of handlers.get(method) ?? []) handler(params);
  });
}

vi.mock("../ipc/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../ipc/client")>();
  return {
    ...actual,
    isEngineConnected: () => true,
    subscribe: (method: string, handler: (params: Record<string, unknown>) => void) => {
      let set = handlers.get(method);
      if (!set) {
        set = new Set();
        handlers.set(method, set);
      }
      set.add(handler);
      return () => set?.delete(handler);
    },
    ipc: {
      ...actual.ipc,
      listRoutines: vi.fn(async () => ({ routines: [] })),
      runRoutine: vi.fn(async () => ({ ok: true, detail: "Done.", answer: "" })),
      deleteRoutine: vi.fn(async () => ({ ok: true })),
    },
  };
});

const ROWS = {
  routines: [
    {
      id: "sums",
      name: "Quick Sums",
      description: "",
      runCount: 0,
      lastRunAt: null,
      variables: [],
    },
  ],
};

function stepUpdate(status: string, extra: Record<string, unknown> = {}) {
  return {
    runId: "run-1",
    routineId: "sums",
    stepId: "s1",
    index: 0,
    total: 1,
    toolId: "calculator",
    label: "Do math and unit conversions",
    status,
    ...extra,
  };
}

describe("watching a routine run from Settings", () => {
  beforeEach(() => {
    handlers.clear();
    vi.mocked(ipc.listRoutines).mockClear();
    vi.mocked(ipc.runRoutine).mockClear();
    vi.mocked(ipc.listRoutines).mockResolvedValue(ROWS as never);
  });

  it("shows each step as its notification arrives, then the answer", async () => {
    // The run only settles once the test lets it: the steps must be on screen
    // WHILE it is still going, which is the whole difference from a report.
    let finish: (value: unknown) => void = () => {};
    vi.mocked(ipc.runRoutine).mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }) as never,
    );

    render(<RoutineLibrary />);
    fireEvent.click(await screen.findByLabelText("Run Quick Sums"));

    emit(Method.RoutineStepUpdate, stepUpdate("running"));
    // The tool's plain label, and the tool id as the machine fact beside it.
    expect(await screen.findByText("Do math and unit conversions")).toBeTruthy();
    expect(screen.getByText("calculator")).toBeTruthy();

    emit(Method.RoutineStepUpdate, stepUpdate("ok"));
    // One step, still one line — the second event UPDATES it rather than
    // appending a second copy of the same step.
    expect(screen.getAllByText("Do math and unit conversions")).toHaveLength(1);

    await act(async () => {
      finish({ ok: true, detail: "Done — every step finished.", answer: "6016" });
    });

    // THE POINT OF THE WHOLE CHANGE.
    expect(await screen.findByText("6016")).toBeTruthy();
    expect(screen.getByText("Done — every step finished.")).toBeTruthy();
  });

  it("says which step failed and why, and doesn't call the run done", async () => {
    vi.mocked(ipc.runRoutine).mockResolvedValue({
      ok: false,
      status: "failed",
      detail: "That step didn't work.",
      answer: "",
    } as never);

    render(<RoutineLibrary />);
    fireEvent.click(await screen.findByLabelText("Run Quick Sums"));

    emit(Method.RoutineStepUpdate, stepUpdate("running"));
    emit(
      Method.RoutineStepUpdate,
      stepUpdate("failed", { message: "That step didn't work." }),
    );

    expect(await screen.findByText("Do math and unit conversions")).toBeTruthy();
    await waitFor(() => {
      // The step's own plain sentence, and the run's terminal state saying what
      // happened rather than "Done".
      expect(screen.getAllByText("That step didn't work.").length).toBeGreaterThan(0);
    });
    expect(screen.queryByText("Done — every step finished.")).toBeNull();
  });

  it("shows a step waiting on a permission card as waiting, not as work", async () => {
    let finish: (value: unknown) => void = () => {};
    vi.mocked(ipc.runRoutine).mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }) as never,
    );

    render(<RoutineLibrary />);
    fireEvent.click(await screen.findByLabelText("Run Quick Sums"));
    emit(Method.RoutineStepUpdate, stepUpdate("running"));

    // The ordinary permission card goes up elsewhere on screen; the panel must
    // not leave the step blinking as though it were working.
    emit(Method.PermissionRequestGrant, { toolId: "calculator", label: "Do math" });
    expect(await screen.findByText("Waiting for your answer.")).toBeTruthy();

    // Answered: the step's next event is the proof, and the waiting line goes.
    emit(Method.RoutineStepUpdate, stepUpdate("ok"));
    await waitFor(() => expect(screen.queryByText("Waiting for your answer.")).toBeNull());

    await act(async () => {
      finish({ ok: true, detail: "Done — every step finished.", answer: "6016" });
    });
  });
});
