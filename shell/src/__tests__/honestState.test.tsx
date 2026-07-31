// Two surfaces that must never claim a fact they don't have.
//
// Both halves here are about the same failure: a row of UI *asserting* something
// about the person's own data that the app has not actually established. It is
// the failure IMPLEMENTATION.md standing rule 1 is written against ("never ship
// a control that displays fabricated state"), and on the restore surface it is
// also a G3 problem — the one screen someone opens after things have gone wrong
// is the last place a comforting guess belongs.
//
//   (a) "restored ✓" — driven through the REAL `useSnapshots` hook, click by
//       click, with `ipc.restoreSnapshot` held open by hand. The tick must be
//       absent while the call is in flight AND after a refused reply, and appear
//       only once the core has answered {ok:true}. The previous test rendered the
//       component twice with `restoredId` set two ways, which proved only that
//       the component reads a prop: an optimistic `setRestoredId(id)` inserted
//       into the hook survived it, and shipped a row saying a restore had landed
//       when the core had just refused it.
//   (b) the routine library's empty state — "None yet" is a claim about the
//       person's saved routines, and while the engine is down the surface never
//       asked. Every sibling section keeps that distinction; this one had lost it.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, act, waitFor } from "@testing-library/react";
import { RestorePointsSection, SnapshotRows } from "../components/SnapshotsCard";
import { RoutineLibrary } from "../components/RoutineLibrary";
import { useSnapshots } from "../hooks/useSnapshots";
import { ipc } from "../ipc/client";
import type { SnapshotRestoreResult } from "../ipc/client";
import type { Snapshot, SnapshotList } from "../types/ui";

afterEach(cleanup);

// Flipped per test; read at call time, never during the factory below.
let engineConnected = true;

vi.mock("../ipc/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../ipc/client")>();
  return {
    ...actual,
    isEngineConnected: () => engineConnected,
    subscribeCoreState: () => () => {},
    ipc: {
      ...actual.ipc,
      listSnapshots: vi.fn(),
      restoreSnapshot: vi.fn(),
      listRoutines: vi.fn(async () => ({ routines: [] })),
      runRoutine: vi.fn(),
    },
  };
});

// ---------------------------------------------------------------------------
// (a) "restored ✓" waits for the core
// ---------------------------------------------------------------------------

const ANCHOR: Snapshot = {
  id: "anchor",
  createdAt: 1_700_000_000,
  trigger: "auto",
  reason: "guard_weakened",
  reasonLabel: "Before turning a guard off",
  verifiedWorking: true,
  undeletable: true,
  capturesBinary: true,
  createdInMode: "safe",
};

// No one-action target, so the only "Restore" on screen is the per-row confirm's.
const LIST: SnapshotList = {
  snapshots: [ANCHOR],
  lastWorkingId: undefined,
  lastWorkingLabel: undefined,
  lastWorkingProfileChange: undefined,
  warning: undefined,
};

/** A promise the test resolves by hand, so "in flight" is a state we can assert
 * in rather than a race we hope to win. */
function deferred<T>() {
  let settle!: (value: T) => void;
  const promise = new Promise<T>((resolve) => {
    settle = resolve;
  });
  return { promise, settle };
}

/** The real hook, wired to the real components — the section for its notice, the
 * rows for their tick. */
function Harness() {
  const state = useSnapshots({ connected: true });
  return (
    <div>
      <RestorePointsSection connected={true} snapshots={state} onOpenAll={() => {}} />
      <SnapshotRows connected={true} snapshots={state} rows={state.snapshots} />
    </div>
  );
}

/** Press "Restore this one", then confirm. Returns once the hook has called out. */
async function pressRestore() {
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: /^Restore this one/ }));
  });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
  });
}

describe("restored ✓ (the real hook, driven by clicks)", () => {
  beforeEach(() => {
    engineConnected = true;
    vi.mocked(ipc.listSnapshots).mockImplementation(async () => LIST);
    vi.mocked(ipc.restoreSnapshot).mockReset();
  });

  it("stays away while the restore is in flight, and after a refusal", async () => {
    const call = deferred<SnapshotRestoreResult>();
    vi.mocked(ipc.restoreSnapshot).mockReturnValue(call.promise);

    render(<Harness />);
    await waitFor(() => expect(screen.getByText(ANCHOR.reasonLabel)).toBeTruthy());
    await pressRestore();

    // The core has been asked and has not answered. Nothing may claim it landed.
    expect(ipc.restoreSnapshot).toHaveBeenCalledWith("anchor");
    expect(screen.queryByText("restored ✓")).toBeNull();

    // Refused — a resolved {ok:false} carrying a plain sentence, never a reject.
    const refusal = "Addison couldn't put your setup back just now. Please try again.";
    await act(async () => {
      call.settle({ ok: false, error: refusal });
      await call.promise;
    });

    // The reply WAS processed (the sentence is on screen), and the row still
    // says nothing about having been restored.
    await waitFor(() => expect(screen.getByText(refusal)).toBeTruthy());
    expect(screen.queryByText("restored ✓")).toBeNull();
    // The way back is still offered, because it never happened.
    expect(screen.getByRole("button", { name: /^Restore this one/ })).toBeTruthy();
  });

  it("appears once the core answers ok, on the row that was restored", async () => {
    const call = deferred<SnapshotRestoreResult>();
    vi.mocked(ipc.restoreSnapshot).mockReturnValue(call.promise);

    render(<Harness />);
    await waitFor(() => expect(screen.getByText(ANCHOR.reasonLabel)).toBeTruthy());
    await pressRestore();
    expect(screen.queryByText("restored ✓")).toBeNull();

    await act(async () => {
      call.settle({ ok: true, snapshotId: "anchor", detail: "Your setup is back." });
      await call.promise;
    });

    await waitFor(() => expect(screen.getByText("restored ✓")).toBeTruthy());
    expect(screen.getByText("Your setup is back.")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// (b) the routine library's empty state
// ---------------------------------------------------------------------------

describe("the routine library with nothing in it", () => {
  beforeEach(() => {
    vi.mocked(ipc.listRoutines).mockClear();
  });

  it("says None yet only when it has actually looked", async () => {
    engineConnected = true;
    render(<RoutineLibrary />);
    await waitFor(() => expect(screen.getByText("None yet")).toBeTruthy());
    expect(screen.getByText("saved steps appear here")).toBeTruthy();
    expect(document.body.textContent).not.toContain("not connected");
  });

  it("never claims the person has no routines while the engine is down", async () => {
    engineConnected = false;
    render(<RoutineLibrary />);
    await waitFor(() =>
      expect(
        screen.getByText(
          "You can see and run your saved routines here once Addison's engine is connected.",
        ),
      ).toBeTruthy(),
    );
    // The count-shaped claim is the bug: a disconnected surface cannot know.
    expect(screen.queryByText("None yet")).toBeNull();
    expect(ipc.listRoutines).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// (c) one routine's answers must never become another's
// ---------------------------------------------------------------------------
//
// The `values` map is component-level, so it outlives the routine it was filled
// for. Routine A asks for `path`; the person answers. Routine B declares `path`
// WITH a default, so it needs no input and skips the fill step entirely — and ran
// with A's answer. The engine cannot catch this: it builds defaults from
// `routine.variables` and then applies whatever the caller sent, so a stray key
// is inert but a name COLLISION silently wins. `path`, `branch` and `message` are
// exactly the names two routines are most likely to share.

describe("routine variable values", () => {
  beforeEach(() => {
    vi.mocked(ipc.listRoutines).mockClear();
    vi.mocked(ipc.runRoutine).mockClear();
  });

  it("never sends one routine's answers to another", async () => {
    // The reachable repro is ABANDONING a fill, not completing one: `executeRun`
    // clears `values` in its finally block, so running A first cleans up after
    // itself. Open A's fill panel, type an answer, then click B's Run instead —
    // B needs no input (its `path` has a default), so it skips the fill step and
    // runs immediately, carrying A's typed answer under the shared name.
    engineConnected = true;
    vi.mocked(ipc.listRoutines).mockResolvedValue({
      routines: [
        { id: "a", name: "Routine A", description: "", variables: [{ name: "path", prompt: "Where?" }] },
        {
          id: "b", name: "Routine B", description: "",
          variables: [{ name: "path", prompt: "Where?", default: "/b/default" }],
        },
      ],
    } as never);
    vi.mocked(ipc.runRoutine).mockResolvedValue({ ok: true, detail: "Done." } as never);

    render(<RoutineLibrary />);
    await waitFor(() => expect(screen.getByText("Routine A")).toBeTruthy());

    // Open A's fill panel and type — but never run A.
    fireEvent.click(screen.getAllByRole("button", { name: /run/i })[0]);
    const input = await screen.findByDisplayValue("");
    fireEvent.change(input, { target: { value: "/a/answer" } });

    // Now run B.
    await act(async () => {
      fireEvent.click(screen.getAllByRole("button", { name: /run/i })[1]);
    });

    const calls = vi.mocked(ipc.runRoutine).mock.calls;
    expect(calls).toHaveLength(1);
    expect(calls[0][0]).toBe("b");
    // B must get nothing — its own default is the engine's job to apply.
    expect(calls[0][1]).toEqual({});
  });
});
