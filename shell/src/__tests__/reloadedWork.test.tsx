// Reopening a chat brings its work back — KNOWN-BUGS #5.
//
// THE DEFECT. "Addison's work" and the "Save as routine" link under it were live
// state and nothing else: `useTurn` collects `tool.activityUpdate` frames into
// `activities`, and `resetTurn` empties them whenever the conversation changes. So
// a quit-and-relaunch (or just switching chats and back) left a turn whose steps
// were on screen a minute ago with no panel and no way to save them — silently,
// which is the part that makes it a defect rather than a limitation.
//
// THE FIX has two halves and this file pins both:
//
//   1. `conversation.load` now answers with `work` — the last turn's steps, in the
//      same {toolId, label, detail?} shape a live activity frame carries — and the
//      parser has to survive whatever arrives, including an older core that sends
//      no such key at all;
//   2. `useConversations` puts those steps back AFTER the transient reset, so the
//      reopened chat's panel is its own last turn and never the previous chat's.
//
// The panel component itself is unchanged, so the third suite asserts only the
// thing that matters here: fed restored steps with no turn running, it offers the
// save link. Its rendering rules have their own suite (activityPanel.test.tsx).

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import { renderHook } from "@testing-library/react";

import { ActivityPanel } from "../components/ActivityPanel";
import type { ActivityUpdate } from "../types/protocol";

// The hook reaches for the IPC client at call time; nothing here should need a
// Tauri context. `parseLoadedConversation` is imported from the real module in the
// suite below, which is why the mock is declared with the factory form (hoisted,
// module-scoped) rather than by stubbing the import site.
vi.mock("../ipc/client", async (importActual) => {
  const actual = await importActual<typeof import("../ipc/client")>();
  return {
    ...actual,
    isEngineConnected: () => true,
    ipc: {
      loadConversation: vi.fn(),
      listConversations: vi.fn().mockResolvedValue([]),
    },
  };
});

import { ipc, parseLoadedConversation } from "../ipc/client";
import { useConversations } from "../hooks/useConversations";

afterEach(cleanup);

const RESTORED: ActivityUpdate[] = [
  { toolId: "read_web_page", label: "Read a web page", detail: "en.wikipedia.org" },
  { toolId: "spy_tool", label: "Check something for you" },
];

// ---------------------------------------------------------------------------
// The parser. Mirrors rpc/conversation._handle_conversation_load's payload.
// ---------------------------------------------------------------------------
describe("parseLoadedConversation over the work steps", () => {
  it("carries the restored steps across the process boundary", () => {
    const parsed = parseLoadedConversation({
      conversationId: "c1",
      title: "A chat",
      messages: [{ id: "m1", role: "user", content: "go" }],
      work: [
        { toolId: "read_web_page", label: "Read a web page", detail: "en.wikipedia.org" },
        { toolId: "spy_tool", label: "Check something for you" },
      ],
    });
    expect(parsed.work).toEqual(RESTORED);
  });

  it("reads a payload with no work as no steps, not as a crash", () => {
    // Two real cases at once: a last turn that used no tools (the core omits the
    // key rather than sending an empty list), and a core older than this change.
    const parsed = parseLoadedConversation({
      conversationId: "c1",
      title: null,
      messages: [],
    });
    expect(parsed.work).toEqual([]);
  });

  it("drops a garbled step instead of rendering 'undefined' under the rule", () => {
    const parsed = parseLoadedConversation({
      conversationId: "c1",
      title: null,
      messages: [],
      work: [
        { toolId: "spy_tool" },                       // no label — nothing to show
        { label: "" },                                // empty label, same
        "not even a record",
        { toolId: "spy_tool", label: "Check something for you", detail: "  " },
      ],
    });
    // Only the last survives, and its whitespace-only detail became an ABSENT
    // property rather than a blank mono line under the step.
    expect(parsed.work).toEqual([{ toolId: "spy_tool", label: "Check something for you" }]);
  });
});

// ---------------------------------------------------------------------------
// The wiring. Opening a conversation must restore its steps, and must do it after
// the reset that clears the previous chat's.
// ---------------------------------------------------------------------------
function harness() {
  const setActivities = vi.fn();
  const setMessages = vi.fn();
  const calls: string[] = [];
  const args = {
    connected: true,
    controlsBusy: false,
    resetTransientState: () => calls.push("reset"),
    setMessages,
    setActivities: (...a: unknown[]) => {
      calls.push("setActivities");
      return setActivities(...a);
    },
    setScreen: vi.fn(),
    setStatusBanner: vi.fn(),
  } as unknown as Parameters<typeof useConversations>[0];
  return { args, setActivities, calls };
}

describe("opening a stored conversation", () => {
  it("restores the last turn's steps, after the transient reset", async () => {
    const loadConversation = ipc.loadConversation as unknown as ReturnType<typeof vi.fn>;
    // The RAW core payload through the REAL parser, which is what `ipc.
    // loadConversation` does — mocking a hand-built parsed object would prove the
    // hook agrees with our memory of the wire instead of with the wire.
    loadConversation.mockResolvedValue(
      parseLoadedConversation({
        conversationId: "c1",
        title: "A chat",
        messages: [{ id: "m1", role: "user", content: "go" }],
        work: RESTORED,
      }),
    );
    const { args, setActivities, calls } = harness();
    const { result } = renderHook(() => useConversations(args));

    result.current.handleOpenConversation("c1");

    await waitFor(() => expect(setActivities).toHaveBeenCalledWith(RESTORED));
    // ORDER IS THE ASSERTION. resetTransientState calls useTurn's resetTurn, which
    // empties `activities`; restoring first would be silently undone.
    expect(calls).toEqual(["reset", "setActivities"]);
  });

  it("leaves the panel empty for a chat whose last turn did no work", async () => {
    const loadConversation = ipc.loadConversation as unknown as ReturnType<typeof vi.fn>;
    loadConversation.mockResolvedValue(
      parseLoadedConversation({
        conversationId: "c2",
        title: "Just talking",
        messages: [{ id: "m1", role: "user", content: "hello" }],
        // no `work` key — the parser's fallback is what reaches the hook
      }),
    );
    const { args, setActivities } = harness();
    const { result } = renderHook(() => useConversations(args));

    result.current.handleOpenConversation("c2");

    await waitFor(() => expect(setActivities).toHaveBeenCalledWith([]));
  });
});

// ---------------------------------------------------------------------------
// The point of all of it: the link is back.
// ---------------------------------------------------------------------------
describe("the work panel rebuilt from history", () => {
  it("lists the restored steps and offers to save them", () => {
    render(
      <ActivityPanel
        isWorking={false}
        current={null}
        activities={RESTORED}
        onProposeRoutine={vi.fn()}
      />,
    );
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Save as routine" })).toBeTruthy();
  });
});
