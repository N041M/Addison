// Restore points (GLOBAL FLOOR G3) — the frontend guards.
//
// Two halves, like skills.test.ts + renameConversation.test.tsx:
//   (a) `parseSnapshotList` fails CLOSED. A row we can't identify or date is a
//       row the card could offer as a way back and then fail to restore, so it
//       is dropped rather than shown.
//   (b) the surface itself, rendered for real, because three of its properties
//       are promises the core cannot keep on its own: the restore is behind a
//       two-step INLINE confirm (never a browser dialog), a permanent G4 row
//       offers no Remove control, and a failed restore says one plain sentence
//       instead of a stack trace.
//
// REDESIGN NOTE (dark direction, phase 3): the single "Restore points" card
// became two pieces — `RestorePointsSection` (the Settings section: the
// one-action summary row + the honest no-target sentences) and `SnapshotRows`
// (the list, shared by the Restore-points modal and the Snapshots surface). The
// assertions below are unchanged in MEANING; each now renders whichever piece
// owns the behaviour it is about. Two mechanical consequences:
//   * the one-action control is an accent "restore" row action whose accessible
//     name is still "Restore to the last working state" — the name is what the
//     test pins, because that is what a screen reader announces;
//   * the Settings section no longer lists the rows, so the target name appears
//     once there rather than twice.
//
// The component half uses React.createElement rather than JSX so this file can
// keep the `.ts` name the contract's ownership plan froze.

import { describe, it, expect, vi, afterEach } from "vitest";
import { createElement } from "react";
import { render, screen, fireEvent, cleanup, act } from "@testing-library/react";
import { parseSnapshotList } from "../ipc/client";
import { RestorePointsSection, SnapshotRows, formatWhen } from "../components/SnapshotsCard";
import type { SnapshotsState } from "../hooks/useSnapshots";
import type { Snapshot } from "../types/ui";

// globals:false → testing-library's automatic cleanup isn't registered.
afterEach(cleanup);

const CONSEQUENCE =
  "Your settings, services, notes, widgets and routines go back to how they were. " +
  "Your chats and your saved keys aren't touched.";

describe("parseSnapshotList", () => {
  it("round-trips a realistic snapshot.list payload", () => {
    const parsed = parseSnapshotList({
      snapshots: [
        {
          id: "s2",
          createdAt: 1_700_000_100,
          trigger: "on_command",
          reason: "user_request",
          reasonLabel: "You saved this",
          verifiedWorking: true,
          undeletable: false,
          capturesBinary: false,
          createdInMode: "safe",
        },
      ],
      lastWorkingId: "s2",
      lastWorkingLabel: "You saved this",
      lastWorkingProfileChange: null,
    });
    expect(parsed.snapshots).toEqual([
      {
        id: "s2",
        createdAt: 1_700_000_100,
        trigger: "on_command",
        reason: "user_request",
        reasonLabel: "You saved this",
        verifiedWorking: true,
        undeletable: false,
        capturesBinary: false,
        createdInMode: "safe",
      },
    ]);
    expect(parsed.lastWorkingId).toBe("s2");
    // A null profile-change is "no profile change", not a sentence to render.
    expect(parsed.lastWorkingProfileChange).toBeUndefined();
    expect(parsed.warning).toBeUndefined();
  });

  it("drops rows with no usable id or timestamp", () => {
    const parsed = parseSnapshotList({
      snapshots: [
        { createdAt: 10, reasonLabel: "No id" },
        { id: "", createdAt: 10, reasonLabel: "Empty id" },
        { id: "no-date", reasonLabel: "No timestamp" },
        { id: "bad-date", createdAt: "yesterday", reasonLabel: "Junk timestamp" },
        { id: "nan-date", createdAt: Number.NaN, reasonLabel: "NaN timestamp" },
        "not an object",
        { id: "keeper", createdAt: 10, reasonLabel: "Working setup" },
      ],
    });
    expect(parsed.snapshots.map((s) => s.id)).toEqual(["keeper"]);
  });

  it("tolerates snake_case createdInMode", () => {
    const parsed = parseSnapshotList({
      snapshots: [{ id: "a", createdAt: 1, created_in_mode: "open" }],
    });
    expect(parsed.snapshots[0].createdInMode).toBe("open");
  });

  it("never shows a raw reason slug, and never claims a flag the core didn't set", () => {
    const parsed = parseSnapshotList({ snapshots: [{ id: "a", createdAt: 1, reason: "genesis" }] });
    expect(parsed.snapshots[0].reasonLabel).toBe("Before a change");
    // Claiming a point is verified-working, permanent, or version-stamped when
    // the core didn't say so would each be a promise the floor can't keep.
    expect(parsed.snapshots[0].verifiedWorking).toBe(false);
    expect(parsed.snapshots[0].undeletable).toBe(false);
    expect(parsed.snapshots[0].capturesBinary).toBe(false);
  });

  it("degrades on junk instead of throwing", () => {
    for (const junk of [null, undefined, 42, "nope", {}, { snapshots: "no" }]) {
      expect(parseSnapshotList(junk)).toEqual({
        snapshots: [],
        lastWorkingId: undefined,
        lastWorkingLabel: undefined,
        lastWorkingProfileChange: undefined,
        warning: undefined,
      });
    }
  });
});

// --- the card ---------------------------------------------------------------

const ROW: Snapshot = {
  id: "s1",
  createdAt: 1_700_000_000,
  trigger: "auto",
  reason: "turn_verified",
  reasonLabel: "Working setup",
  verifiedWorking: true,
  undeletable: false,
  capturesBinary: false,
  createdInMode: "safe",
};

function stateWith(over: Partial<SnapshotsState> = {}): SnapshotsState {
  return {
    snapshots: [ROW],
    snapshotsLoaded: true,
    lastWorkingId: "s1",
    lastWorkingLabel: "Working setup",
    lastWorkingProfileChange: undefined,
    warning: undefined,
    notice: null,
    restoredId: undefined,
    busy: false,
    refreshSnapshots: vi.fn(),
    handleCreateSnapshot: vi.fn(async () => {}),
    handleRestoreLastWorking: vi.fn(async () => {}),
    handleRestoreSnapshot: vi.fn(async () => {}),
    handleDeleteSnapshot: vi.fn(async () => {}),
    ...over,
  };
}

/** The Settings section: the one-action way back and the honest silences. */
function renderCard(state: SnapshotsState) {
  render(
    createElement(RestorePointsSection, {
      connected: true,
      snapshots: state,
      onOpenAll: vi.fn(),
    }),
  );
}

/** The shared list of rows — what the Restore-points modal and the Snapshots
 * surface both render. Permanent-row semantics live here. */
function renderRows(state: SnapshotsState, connected = true) {
  render(
    createElement(SnapshotRows, { connected, snapshots: state, rows: state.snapshots }),
  );
}

describe("the restore points card", () => {
  it("names the target before offering the button — never a click into the dark", () => {
    renderCard(stateWith());
    // Named once, in the summary row; the section itself no longer lists rows.
    expect(screen.getAllByText("Working setup")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Restore to the last working state" })).toBeTruthy();
  });

  it("keeps the target and its timestamp on screen THROUGH the confirm", () => {
    // The press itself is the moment that matters: if the naming line is swapped
    // out for the consequence copy, the person confirms with the target off
    // screen — a recovery pressed blind, which is the one thing this surface
    // exists to prevent (HANDOFF: "the target always named with its timestamp
    // before the button"). Regression guard: an earlier build gated this row
    // behind `!confirming`.
    const when = formatWhen(ROW.createdAt);
    expect(when).not.toBe("");
    renderCard(stateWith());
    expect(screen.getByText("Working setup")).toBeTruthy();
    expect(screen.getByText(when)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Restore to the last working state" }));
    expect(screen.getByText(CONSEQUENCE)).toBeTruthy();
    expect(screen.getByText("Working setup")).toBeTruthy();
    expect(screen.getByText(when)).toBeTruthy();
    // And only one live restore control while the confirm is up.
    expect(
      screen.queryByRole("button", { name: "Restore to the last working state" }),
    ).toBeNull();
  });

  it("requires the two-step inline confirm, and never a browser confirm()", () => {
    const confirmSpy = vi.spyOn(window, "confirm");
    const state = stateWith();
    renderCard(state);

    fireEvent.click(screen.getByRole("button", { name: "Restore to the last working state" }));
    // Step one only tells the person what will happen; nothing has run yet.
    expect(state.handleRestoreLastWorking).not.toHaveBeenCalled();
    expect(screen.getByText(CONSEQUENCE)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    expect(state.handleRestoreLastWorking).toHaveBeenCalledTimes(1);
    expect(confirmSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("lets the person back out of the confirm without restoring", () => {
    const state = stateWith();
    renderCard(state);
    fireEvent.click(screen.getByRole("button", { name: "Restore to the last working state" }));
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    expect(state.handleRestoreLastWorking).not.toHaveBeenCalled();
    expect(screen.queryByText(CONSEQUENCE)).toBeNull();
  });

  it("says so when the restore will also change the profile", () => {
    const sentence =
      "This restore point was saved in Developer mode, so Addison will switch back to Developer.";
    renderCard(stateWith({ lastWorkingProfileChange: sentence }));
    fireEvent.click(screen.getByRole("button", { name: "Restore to the last working state" }));
    // Appended to the base copy, never substituted for it.
    expect(screen.getByText(`${CONSEQUENCE} ${sentence}`)).toBeTruthy();
  });

  it("warns that restoring to the first point clears everything set up since", () => {
    renderCard(stateWith({ lastWorkingLabel: "Addison as first installed" }));
    fireEvent.click(screen.getByRole("button", { name: "Restore to the last working state" }));
    expect(
      screen.getByText(
        `${CONSEQUENCE} This is Addison as it was first installed, so your services, notes, widgets and routines are cleared.`,
      ),
    ).toBeTruthy();
  });

  it("renders no delete control on a permanent (G4) row, and marks it as permanent", () => {
    const anchor: Snapshot = {
      ...ROW,
      id: "anchor",
      reasonLabel: "Before turning a guard off",
      undeletable: true,
      capturesBinary: true,
    };
    renderRows(stateWith({ snapshots: [anchor, ROW] }));
    expect(screen.getByText("Permanent")).toBeTruthy();
    // Exactly one Remove button: the ordinary row's. Offering one on the anchor
    // would make a guarantee look like a bug the moment it was pressed.
    expect(screen.getAllByRole("button", { name: /^Remove/ })).toHaveLength(1);
  });

  it("renders a failed restore as one plain sentence, not a stack trace", () => {
    renderCard(
      stateWith({ notice: "Addison couldn't put your setup back just now. Please try again." }),
    );
    const text = document.body.textContent ?? "";
    expect(text).toContain("Addison couldn't put your setup back just now. Please try again.");
    expect(text).not.toContain("Traceback");
    expect(text).not.toContain("Error:");
  });

  it("shows the sticky warning when an automatic restore point couldn't be saved", () => {
    const warning =
      "Addison couldn't save a restore point just now. Your older restore points are still there.";
    renderCard(stateWith({ warning }));
    expect(screen.getByText(warning)).toBeTruthy();
  });

  it("offers no restore button at all when there is nothing to go back to", () => {
    renderCard(stateWith({ snapshots: [], lastWorkingId: undefined, lastWorkingLabel: undefined }));
    expect(screen.queryByRole("button", { name: "Restore to the last working state" })).toBeNull();
  });
});

// --- the per-row "Restore this one" on permanent rows (step 2, contract D7) --
//
// Permanent rows (the G4 anchors + genesis) get their own by-id restore — the
// points most worth being able to return to by name. Ordinary rows do NOT; they
// keep only Remove. Same two-step inline confirm idiom, names the row before the
// click, never a browser dialog, never the danger token. These use a state with
// NO one-action target, so the only "Restore" button on screen is the per-row
// confirm's.

const ANCHOR: Snapshot = {
  ...ROW,
  id: "anchor",
  reasonLabel: "Before turning a guard off",
  undeletable: true,
  capturesBinary: true,
};
/** A second permanent row, an hour later — a different id, a different label and
 * a different timestamp, so a by-id restore that quietly used the first row in
 * the list (or the first row's date) cannot pass. */
const SECOND_ANCHOR: Snapshot = {
  ...ANCHOR,
  id: "anchor-2",
  createdAt: ROW.createdAt + 3_600,
  reasonLabel: "Before turning the second guard off",
};
const NO_ONE_ACTION_TARGET = { lastWorkingId: undefined, lastWorkingLabel: undefined } as const;

describe("the per-row restore on permanent rows", () => {
  it("offers Restore this one on permanent rows only; ordinary rows keep Remove", () => {
    renderRows(stateWith({ snapshots: [ANCHOR, ROW], ...NO_ONE_ACTION_TARGET }));
    expect(screen.getAllByRole("button", { name: /^Restore this one/ })).toHaveLength(1);
    // The ordinary row keeps Remove; the permanent one never shows it.
    expect(screen.getAllByRole("button", { name: /^Remove/ })).toHaveLength(1);
  });

  it("names the row, then restores THAT row by id — never simply the first one", () => {
    // TWO permanent rows, and the SECOND is the one pressed. With a single-row
    // list `rows[0].id` and `snap.id` are the same string, so the worst failure
    // this surface can have — confirm one restore point, restore a different one
    // — was indistinguishable from correct behaviour.
    const confirmSpy = vi.spyOn(window, "confirm");
    const state = stateWith({ snapshots: [ANCHOR, SECOND_ANCHOR], ...NO_ONE_ACTION_TARGET });
    renderRows(state);

    fireEvent.click(
      screen.getByRole("button", { name: `Restore this one: ${SECOND_ANCHOR.reasonLabel}` }),
    );
    // Step one names the row and shows the consequence; nothing has run.
    expect(state.handleRestoreSnapshot).not.toHaveBeenCalled();
    expect(screen.getByText(CONSEQUENCE)).toBeTruthy();
    // The row the person pressed is named in the confirm as well as in its list
    // row; the other row is still named exactly once, in its own row.
    expect(screen.getAllByText(SECOND_ANCHOR.reasonLabel)).toHaveLength(2);
    expect(screen.getAllByText(ANCHOR.reasonLabel)).toHaveLength(1);
    // …with the pressed row's own timestamp beside it, not its neighbour's.
    expect(screen.getAllByText(formatWhen(SECOND_ANCHOR.createdAt))).toHaveLength(2);
    expect(screen.getAllByText(formatWhen(ANCHOR.createdAt))).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "Restore" }));
    expect(state.handleRestoreSnapshot).toHaveBeenCalledWith(SECOND_ANCHOR.id);
    expect(state.handleRestoreSnapshot).not.toHaveBeenCalledWith(ANCHOR.id);
    // The one-action restore is untouched — this is the by-id path.
    expect(state.handleRestoreLastWorking).not.toHaveBeenCalled();
    expect(confirmSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it("lets the person back out of a per-row restore without restoring", () => {
    const state = stateWith({ snapshots: [ANCHOR], ...NO_ONE_ACTION_TARGET });
    renderRows(state);
    fireEvent.click(screen.getByRole("button", { name: /^Restore this one/ }));
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    expect(state.handleRestoreSnapshot).not.toHaveBeenCalled();
    expect(screen.queryByText(CONSEQUENCE)).toBeNull();
    // The trigger is back.
    expect(screen.getByRole("button", { name: /^Restore this one/ })).toBeTruthy();
  });

  it("marks ONLY the row the hook says was restored, and retires its trigger", () => {
    // This is the component half: `restoredId` names one row, so exactly that
    // row gets the tick and loses its trigger while its neighbour keeps both.
    // (Rendering with the prop set two ways proves only that the component reads
    // a prop — that the tick waits for a REAL {ok:true} is a property of the hook
    // and is driven click-by-click in restoreFeedback.test.tsx.)
    renderRows(stateWith({ snapshots: [ANCHOR, SECOND_ANCHOR], ...NO_ONE_ACTION_TARGET }));
    expect(screen.queryByText("restored ✓")).toBeNull();
    expect(screen.getAllByRole("button", { name: /^Restore this one/ })).toHaveLength(2);
    cleanup();

    renderRows(
      stateWith({
        snapshots: [ANCHOR, SECOND_ANCHOR],
        restoredId: SECOND_ANCHOR.id,
        ...NO_ONE_ACTION_TARGET,
      }),
    );
    expect(screen.getAllByText("restored ✓")).toHaveLength(1);
    const triggers = screen.getAllByRole("button", { name: /^Restore this one/ });
    expect(triggers.map((b) => b.getAttribute("aria-label"))).toEqual([
      `Restore this one: ${ANCHOR.reasonLabel}`,
    ]);
  });

  it("warns that restoring the genesis point clears everything, in the per-row confirm", () => {
    const genesis: Snapshot = {
      ...ROW,
      id: "genesis",
      reasonLabel: "Addison as first installed",
      undeletable: true,
    };
    renderRows(stateWith({ snapshots: [genesis], ...NO_ONE_ACTION_TARGET }));
    fireEvent.click(screen.getByRole("button", { name: /^Restore this one/ }));
    expect(
      screen.getByText(
        `${CONSEQUENCE} This is Addison as it was first installed, so your services, notes, widgets and routines are cleared.`,
      ),
    ).toBeTruthy();
  });
});

// --- a restore is never styled with the danger token (HANDOFF rule) ---------
//
// "Going back to a setup that worked is the opposite of a destructive act; the
// rose token is for deleting things" (this file's header, and the HANDOFF rule
// it quotes). Nothing pinned it, so a restore control could quietly be re-toned
// to `danger` and every test would still pass — and the person who most needs to
// press it would be looking at a control coloured like the one that destroys
// things. `RowAction`'s danger tone is the only source of a `danger` class in a
// row action, so its ABSENCE on every restore control is the assertion, with the
// Remove button as the positive control that keeps it from being vacuous.

describe("the colour of a restore", () => {
  const isDanger = (b: HTMLElement) => /danger/.test(b.className);

  it("tones every restore control as accent, never danger", () => {
    renderCard(stateWith());
    const trigger = screen.getByRole("button", { name: "Restore to the last working state" });
    expect(trigger.className).toContain("text-accent");
    expect(isDanger(trigger)).toBe(false);

    fireEvent.click(trigger);
    const confirm = screen.getByRole("button", { name: "Restore" });
    expect(confirm.className).toContain("text-accent");
    expect(isDanger(confirm)).toBe(false);
    cleanup();

    renderRows(stateWith({ snapshots: [ANCHOR, ROW], ...NO_ONE_ACTION_TARGET }));
    const perRow = screen.getByRole("button", { name: /^Restore this one/ });
    expect(perRow.className).toContain("text-accent");
    expect(isDanger(perRow)).toBe(false);

    fireEvent.click(perRow);
    expect(isDanger(screen.getByRole("button", { name: "Restore" }))).toBe(false);

    // The positive control: Remove really does destroy something, so it DOES
    // carry the token. Without this the assertions above could pass on a build
    // where the token had simply been renamed.
    expect(isDanger(screen.getByRole("button", { name: /^Remove/ }))).toBe(true);
  });
});

// --- the two honest silences (G3): restore points exist, no target ----------
//
// When the walk has no target, `canRestore` is false and the whole target/button
// block renders nothing. If restore points are ALSO listed, that silence reads as
// "the floor is broken" to a 54- or 68-year-old — so the card must say which of
// the two truthful states it is in. These pin BOTH sentences byte-for-byte and
// prove the restore button is absent in each, and that the healthy state (a real
// target) shows NEITHER line — the guard against the copy leaking upward.

const SILENCE_UNVERIFIED =
  "None of these has been seen working yet, so the restore button isn't ready. " +
  "It appears after Addison next answers you.";
const SILENCE_IDENTICAL =
  "Your setup already matches your last working setup, so there's nothing to go back to right now.";

describe("the restore points card — no target, but points exist", () => {
  const NO_TARGET = { lastWorkingId: undefined, lastWorkingLabel: undefined } as const;

  it("says the button isn't ready when no restore point has been seen working", () => {
    const unverified: Snapshot = { ...ROW, verifiedWorking: false };
    renderCard(stateWith({ snapshots: [unverified], ...NO_TARGET }));
    expect(screen.getByText(SILENCE_UNVERIFIED)).toBeTruthy();
    expect(screen.queryByText(SILENCE_IDENTICAL)).toBeNull();
    expect(screen.queryByRole("button", { name: "Restore to the last working state" })).toBeNull();
  });

  it("says the setup already matches when a point is verified but there's no target", () => {
    // ROW.verifiedWorking is true; a verified row with no walk target is the
    // 'identical' outcome (and, on this wire, an 'unreadable' walk too).
    renderCard(stateWith({ snapshots: [ROW], ...NO_TARGET }));
    expect(screen.getByText(SILENCE_IDENTICAL)).toBeTruthy();
    expect(screen.queryByText(SILENCE_UNVERIFIED)).toBeNull();
    expect(screen.queryByRole("button", { name: "Restore to the last working state" })).toBeNull();
  });

  it("shows NEITHER silence when there is a real restore target", () => {
    // The healthy state: a target is named and the button is offered. Neither
    // no-target line may leak into it.
    renderCard(stateWith());
    expect(screen.queryByText(SILENCE_UNVERIFIED)).toBeNull();
    expect(screen.queryByText(SILENCE_IDENTICAL)).toBeNull();
    expect(screen.getByRole("button", { name: "Restore to the last working state" })).toBeTruthy();
  });

  it("shows NEITHER silence when the list is empty or disconnected", () => {
    // The existing empty-state / disconnected copy owns those; the silences are
    // only for "points exist, no target".
    renderCard(stateWith({ snapshots: [], ...NO_TARGET }));
    expect(screen.queryByText(SILENCE_UNVERIFIED)).toBeNull();
    expect(screen.queryByText(SILENCE_IDENTICAL)).toBeNull();

    cleanup();
    render(
      createElement(RestorePointsSection, {
        connected: false,
        snapshots: stateWith({ snapshots: [ROW], ...NO_TARGET }),
        onOpenAll: vi.fn(),
      }),
    );
    expect(screen.queryByText(SILENCE_UNVERIFIED)).toBeNull();
    expect(screen.queryByText(SILENCE_IDENTICAL)).toBeNull();
  });
});

describe("the save control", () => {
  it("asks the core to save a restore point", async () => {
    const state = stateWith();
    const { SaveSnapshotAction } = await import("../components/SnapshotsCard");
    render(createElement(SaveSnapshotAction, { connected: true, snapshots: state }));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "save one now" }));
    });
    expect(state.handleCreateSnapshot).toHaveBeenCalledTimes(1);
  });
});

// --- the staged per-id restore ----------------------------------------------
//
// `ipc.restoreSnapshot` has no caller in step 1 (see the comment on it in
// client.ts): the Settings card ships the one-action "last working state"
// restore and a per-row Remove, and no per-row Restore. It is kept for step 2's
// Custom-profile anchor path, which restores one specific point by id.
//
// This is COVERAGE, not a regression guard — it locks the frame an untested
// staged wrapper would otherwise be free to get wrong between now and its first
// real caller. `snapshot.restore` is a frozen method string (contract §11.3
// item 7) and the id has to reach the core under the key `id`; both are
// silent-failure shapes, so they get asserted rather than assumed.
describe("restoreSnapshot (staged for step 2's anchor path)", () => {
  it("sends snapshot.restore with the id the caller asked for", async () => {
    const listeners: ((frame: unknown) => void)[] = [];
    vi.doMock("@tauri-apps/api/core", () => ({ invoke: vi.fn(async () => undefined) }));
    vi.doMock("@tauri-apps/api/event", () => ({
      listen: vi.fn(async (_name: string, handler: (e: { payload: unknown }) => void) => {
        listeners.push((frame) => handler({ payload: frame }));
        return () => {};
      }),
    }));
    // The module reads `isEngineConnected()` per call, so the Tauri marker has
    // to be on `window` before the call — not merely before the import.
    (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
    vi.resetModules();

    const { ipc } = await import("../ipc/client");
    const { invoke } = await import("@tauri-apps/api/core");

    const inFlight = ipc.restoreSnapshot("s1");
    // Two awaited `listen` calls sit between the request and the invoke, so let
    // the queue drain rather than counting microtasks.
    await new Promise((resolve) => setTimeout(resolve, 0));

    const sent = vi.mocked(invoke).mock.calls[0];
    expect(sent?.[0]).toBe("send_to_core");
    const frame = (sent?.[1] as { frame: Record<string, unknown> }).frame;
    expect(frame.method).toBe("snapshot.restore");
    expect(frame.params).toEqual({ id: "s1" });

    // Answer it, so the pending request resolves and its 120 s timeout clears
    // rather than being left dangling for the rest of the run.
    listeners[0]?.({ jsonrpc: "2.0", id: frame.id, result: { ok: true, snapshotId: "s1" } });
    await expect(inFlight).resolves.toMatchObject({ ok: true, snapshotId: "s1" });

    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
    vi.doUnmock("@tauri-apps/api/core");
    vi.doUnmock("@tauri-apps/api/event");
    vi.resetModules();
  });
});
