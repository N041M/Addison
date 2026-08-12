// The sidebar's status footer — the mono line under Settings that reads
// "Simple profile · local · open".
//
// It used to be a CLAIM MADE BEFORE THE ANSWER: `profileLabel` fell through to
// "Simple profile" whenever the profile state was still null, so every launch
// asserted a default as fact for the seconds before `profile.get` came back, and
// then corrected itself in front of the person. A Developer-profile user watched
// their own window say Simple. The QA artifact (§01) accepts silence there, and
// accepts the profile that is actually active — nothing else.
//
// Two halves, because the bug had two halves:
//   (A) the derivation, through the REAL App with `profile.get` held open by hand,
//       so "before the answer" is a state the test stands in rather than a race.
//   (B) the component's own contract: no label → no note at all.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, cleanup, act, waitFor } from "@testing-library/react";

const engine = vi.hoisted(() => ({ up: true }));

vi.mock("../ipc/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../ipc/client")>();
  return {
    ...actual,
    isEngineConnected: () => engine.up,
    subscribeCoreState: () => () => {},
    subscribeStatus: () => () => {},
    subscribeDiagnostics: () => () => {},
    subscribe: () => () => {},
    ipc: {
      ...actual.ipc,
      getProfile: vi.fn(),
      listWorkspaceRoots: vi.fn(async () => []),
    },
  };
});

// Imported AFTER the mock so these are the mocked functions.
import { ipc } from "../ipc/client";
import { App } from "../App";
import { Sidebar } from "../components/Sidebar";

afterEach(cleanup);

/** jsdom ships no matchMedia and the layout keys off one. Wide by default. */
function stubMatchMedia() {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

/** A promise the test settles by hand — "the engine has not answered yet" is a
 * state to assert in, not a window to hit. */
function deferred<T>() {
  let settle!: (value: T) => void;
  const promise = new Promise<T>((resolve) => {
    settle = resolve;
  });
  return { promise, settle };
}

beforeEach(() => {
  stubMatchMedia();
  localStorage.clear();
  Element.prototype.scrollIntoView = () => {};
  engine.up = true;
  vi.mocked(ipc.getProfile).mockReset();
});

// ===========================================================================
// (A) The derivation, through the real App
// ===========================================================================

describe("the status footer before the engine has answered", () => {
  it("says nothing at all, then says what is actually active", async () => {
    // Kills: `profile?.activeProfile === "developer" ? … : "Simple profile"`,
    // whose else-branch is a default asserted as fact.
    const call = deferred<unknown>();
    vi.mocked(ipc.getProfile).mockReturnValue(call.promise as never);

    render(<App />);
    await waitFor(() => expect(ipc.getProfile).toHaveBeenCalled());

    // Asked, unanswered: the line exists (so nothing jumps when the answer
    // lands) and claims nothing.
    const note = screen.getByTestId("profile-note");
    expect(note.textContent?.trim()).toBe("");
    expect(document.body.textContent).not.toContain("profile ·");

    await act(async () => {
      call.settle({ activeProfile: "developer", profiles: [], flags: {}, mode: "open" });
      await call.promise;
    });

    await waitFor(() =>
      expect(screen.getByTestId("profile-note").textContent).toBe(
        "Developer profile · local · open",
      ),
    );
  });

  it("never flashes Simple at a Developer-profile person", async () => {
    // The bug as the person met it: the wrong profile, asserted, then corrected.
    // Nothing may say "Simple" between the first paint and the real answer.
    const call = deferred<unknown>();
    vi.mocked(ipc.getProfile).mockReturnValue(call.promise as never);

    render(<App />);
    await waitFor(() => expect(ipc.getProfile).toHaveBeenCalled());
    expect(document.body.textContent).not.toContain("Simple profile");

    await act(async () => {
      call.settle({ activeProfile: "developer", profiles: [], flags: {}, mode: "open" });
      await call.promise;
    });

    await waitFor(() =>
      expect(document.body.textContent).toContain("Developer profile"),
    );
    expect(document.body.textContent).not.toContain("Simple profile");
  });

  it("keeps saying nothing while the engine is down and was never asked", async () => {
    // A window that opens with no engine has no profile to report, and a guess
    // is no more welcome later in the session than it is in the first second.
    engine.up = false;
    render(<App />);
    await waitFor(() => expect(screen.getByText("Settings")).toBeTruthy());
    expect(ipc.getProfile).not.toHaveBeenCalled();
    expect(screen.getByTestId("profile-note").textContent?.trim()).toBe("");
    expect(document.body.textContent).not.toContain("Simple profile");
  });
});

// ===========================================================================
// (B) The component's own contract
// ===========================================================================

describe("the Sidebar footer note", () => {
  const base = {
    conversations: [],
    currentConversationId: null,
    onOpenConversation: () => {},
    onRenameConversation: () => {},
    onNewChat: () => {},
    newChatDisabled: false,
    view: "chat" as const,
    onOpenSettings: () => {},
  };

  it("renders no note without a label, and the real one with it", () => {
    const { rerender } = render(<Sidebar {...base} />);
    expect(screen.getByTestId("profile-note").textContent?.trim()).toBe("");
    // The spacer line is not something a screen reader should read out.
    expect(screen.getByTestId("profile-note").getAttribute("aria-hidden")).toBe("true");

    rerender(<Sidebar {...base} profileLabel="Simple profile" />);
    const note = screen.getByTestId("profile-note");
    expect(note.textContent).toBe("Simple profile · local");
    expect(note.getAttribute("aria-hidden")).toBeNull();
  });
});
