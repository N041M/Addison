// THE CARD DIES WITH ITS TURN — KNOWN-BUGS #4, owner decision 2026-08-09.
//
// A permission card used to outlive the turn that raised it: Stop was a purely
// local halt, the card stayed on screen with Allow and "Not now" one press away,
// and pressing Allow minutes later ran the tool for a turn the person had ended.
//
// Two halves, and this file is careful about which is which:
//
//   * PRESENTATION (here) — the card goes inert. No Allow, no "Not now", no code
//     box on the arming variant, one plain sentence saying the request ended.
//   * ENFORCEMENT (not here) — the core refuses a late `permission.respond`
//     whatever this side renders, pinned in tests/test_stopped_turn_permissions.py.
//     The only thing this side owes the enforcement is the CALL, so the first test
//     below is that Stop tells the core at all.

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, cleanup, renderHook } from "@testing-library/react";
import { useTurn } from "../hooks/useTurn";
import { PermissionCard } from "../components/PermissionCard";
import { ipc } from "../ipc/client";
import type { PermissionRequest } from "../types/protocol";

vi.mock("../ipc/client", () => ({
  ipc: {
    sendMessage: vi.fn(() => new Promise(() => {})),   // a turn that never lands
    stopTurn: vi.fn(() => Promise.resolve({ ok: true })),
  },
  parseAnsweredWith: () => undefined,
}));

const stopTurn = ipc.stopTurn as unknown as ReturnType<typeof vi.fn>;

/** The dead card's one sentence, pinned byte-for-byte: it is what a person reads
 * instead of the answer they were about to give. */
const EXPIRED_MESSAGE = "This request ended when you stopped the answer.";

const REQUEST: PermissionRequest = {
  toolId: "spy_tool",
  label: "Delete the files in Downloads?",
  description: "This time it wants to run: rm -rf ~/Downloads/*",
  riskTier: "high",
};

function makeArgs() {
  return {
    connected: true,
    setStatusBanner: vi.fn(),
    selectedRole: "primary" as const,
    selectedLocalModel: undefined,
    selectedEffort: undefined,
    effectiveLocalModel: vi.fn(() => undefined),
    effectiveCloudModel: vi.fn(() => "claude-opus-4-8"),
    maybeProposeWidget: vi.fn(),
    maybeProposeOffers: vi.fn(),
    refreshConversations: vi.fn(),
    refreshStats: vi.fn(),
  };
}

beforeEach(() => {
  stopTurn.mockClear();
});
afterEach(cleanup);

describe("Stop and the card it leaves behind", () => {
  it("tells the CORE to stop, which is what refuses a late answer", () => {
    const { result } = renderHook(() => useTurn(makeArgs()));
    act(() => {
      result.current.handleSend("go");
    });
    act(() => {
      result.current.setPermission(REQUEST);
    });
    act(() => {
      result.current.handleStop();
    });
    expect(stopTurn).toHaveBeenCalledTimes(1);
  });

  it("marks the open card expired, and keeps it on screen", () => {
    const { result } = renderHook(() => useTurn(makeArgs()));
    act(() => {
      result.current.setPermission(REQUEST);
    });
    expect(result.current.permissionExpired).toBe(false);
    act(() => {
      result.current.handleStop();
    });
    // The question is not erased — it is the record of what Addison was asking.
    expect(result.current.permission).toEqual(REQUEST);
    expect(result.current.permissionExpired).toBe(true);
  });

  it("does not carry the expiry onto the NEXT card", () => {
    // The same bug with the answers reversed: a fresh card rendering dead on
    // arrival because the flag outlived the card it described.
    const { result } = renderHook(() => useTurn(makeArgs()));
    act(() => {
      result.current.setPermission(REQUEST);
    });
    act(() => {
      result.current.handleStop();
    });
    act(() => {
      result.current.setPermission({ ...REQUEST, toolId: "other_tool" });
    });
    expect(result.current.permissionExpired).toBe(false);
  });

  it("clears the expiry when a new turn starts", () => {
    const { result } = renderHook(() => useTurn(makeArgs()));
    act(() => {
      result.current.setPermission(REQUEST);
    });
    act(() => {
      result.current.handleStop();
    });
    act(() => {
      result.current.handleSend("again");
    });
    expect(result.current.permission).toBeNull();
    expect(result.current.permissionExpired).toBe(false);
  });
});

describe("the expired card", () => {
  it("has no way to answer it, and says why", () => {
    const onRespond = vi.fn();
    render(<PermissionCard request={REQUEST} onRespond={onRespond} expired />);
    // The question survives; the answers do not.
    expect(screen.getByText(REQUEST.label)).toBeTruthy();
    expect(screen.getByText(EXPIRED_MESSAGE)).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByText("Allow")).toBeNull();
    expect(screen.queryByText("Not now")).toBeNull();
    expect(onRespond).not.toHaveBeenCalled();
  });

  it("is muted, not accented — the accent is for what is live", () => {
    const { container } = render(
      <PermissionCard request={REQUEST} onRespond={vi.fn()} expired />,
    );
    const card = container.querySelector("[data-consent-expired]");
    expect(card).toBeTruthy();
    expect(card!.innerHTML).not.toContain("bg-accent");
    expect(card!.querySelector(".text-muted")).toBeTruthy();
  });

  it("takes the ARMING card's code box away too", () => {
    // A ceremony nothing can accept is worse than no ceremony: the box would
    // invite somebody to retype a code for a job that can no longer be armed.
    const arming: PermissionRequest = {
      ...REQUEST,
      label: "Arm Tidy Downloads?",
      arming: {
        nonce: "ACD-EFG",
        automationName: "Tidy Downloads",
        scheduleSentence: "Every day at 9:00",
        command: "/usr/bin/find /Users/mira/Downloads -mtime +30 -delete",
        installPath: "~/Library/LaunchAgents/com.addison.auto.tidy.plist",
        warnings: ["This will run on its own schedule even when Addison is closed."],
        attemptsLeft: 3,
      },
    };
    const { container } = render(
      <PermissionCard request={arming} onRespond={vi.fn()} expired />,
    );
    expect(container.querySelector("input")).toBeNull();
    expect(screen.queryByText("ACD-EFG")).toBeNull();
    expect(screen.queryByText("Arm it")).toBeNull();
    expect(screen.getByText(EXPIRED_MESSAGE)).toBeTruthy();
  });

  it("is still the LIVE card when nothing was stopped", () => {
    // The freeze: an ordinary card renders exactly what it always did.
    render(<PermissionCard request={REQUEST} onRespond={vi.fn()} />);
    expect(screen.getByText("Allow")).toBeTruthy();
    expect(screen.getByText("Not now")).toBeTruthy();
    expect(screen.queryByText(EXPIRED_MESSAGE)).toBeNull();
  });
});
