// The two things a person can now SEE about a continued chat (§4.8, the two
// KNOWN-GAPS entries closed 2026-08-22).
//
// A long conversation is condensed at the end of a turn: Addison summarises the
// older part and carries on in a NEW conversation that records where it came
// from. Until now the only sign of that was one sentence on the Activity Panel
// note channel, which `useTurn` clears at the start of every turn and nothing
// persists — so a chat reopened the next morning showed no boundary at all, and
// the history list showed two rows sharing a title with nothing saying one came
// from the other. Both facts were already on disk (`summary`,
// `continued_from_conversation_id`); nothing rendered them.
//
// So the assertions here are all about DURABILITY and about not hiding anything:
//
//   * the payloads carry the boundary, and the hook holds it for the thread;
//   * the thread draws it from that stored row, above the first message, with the
//     summary behind a disclosure — no accent, no new furniture, because it is a
//     fact about the chat rather than an action or a live state;
//   * the sidebar draws one continued chat as ONE entry, counted once, with the
//     older part indented beneath it — and BOTH conversations stay in the list
//     and stay clickable, because the original transcript's reachability is the
//     promise the whole feature rests on.
//
// Mermaid is stubbed for the same reason chatThread.test.tsx stubs it: it pulls a
// heavy async renderer into jsdom and is irrelevant to any of this.

import { describe, it, expect, vi, beforeAll, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor, renderHook } from "@testing-library/react";

vi.mock("../components/MermaidDiagram", () => ({
  MermaidDiagram: () => null,
}));

// Same factory-form mock as reloadedWork.test.tsx: the hook reaches for the IPC
// client at call time, and the real parsers are imported from the actual module
// below so the payloads under test are the ones the wire carries.
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
import { ChatThread, resetThreadStaggerForTests } from "../components/ChatThread";
import { Sidebar, lineageEntries } from "../components/Sidebar";
import { parseConversationSummaries } from "../types/ui";
import type { ConversationSummary, DisplayMessage } from "../types/ui";

// jsdom ships no layout and so no scrollIntoView; the thread's keep-the-newest-
// line-in-view effect calls it.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

// globals:false → no automatic cleanup is registered.
afterEach(() => {
  cleanup();
  resetThreadStaggerForTests();
});

const SUMMARY =
  "The person is planning a move to Brno and asked about packing, dates and the cost " +
  "of a van. Nothing is booked yet.";

const MESSAGES: DisplayMessage[] = [
  { id: "m1", role: "user", content: "Where were we?" },
  { id: "m2", role: "assistant", content: "Packing, and the van.", pending: false },
];

// ---------------------------------------------------------------------------
// The wire. Mirrors rpc/conversation.py — both keys are sent ONLY for a
// continuation, so an ordinary chat's payload is byte-identical to before.
// ---------------------------------------------------------------------------
describe("the boundary on the wire", () => {
  it("carries the lineage and the summary off a loaded continuation", () => {
    const loaded = parseLoadedConversation({
      conversationId: "c2",
      title: "Moving to Brno",
      messages: [{ id: "m1", role: "user", content: "Where were we?" }],
      continuedFrom: "c1",
      summary: SUMMARY,
    });
    expect(loaded.continuedFrom).toBe("c1");
    expect(loaded.summary).toBe(SUMMARY);
  });

  it("reads an ordinary chat as no boundary at all", () => {
    const loaded = parseLoadedConversation({
      conversationId: "c1",
      title: "Moving to Brno",
      messages: [{ id: "m1", role: "user", content: "hello" }],
    });
    expect(loaded.continuedFrom).toBeNull();
    expect(loaded.summary).toBeNull();
  });

  it("keeps a summary-less continuation a continuation", () => {
    // The marker still says a boundary is here; there is simply nothing behind
    // the disclosure. Saying nothing would be the worse answer.
    const loaded = parseLoadedConversation({
      conversationId: "c2",
      messages: [],
      continuedFrom: "c1",
      summary: "   ",
    });
    expect(loaded.continuedFrom).toBe("c1");
    expect(loaded.summary).toBeNull();
  });

  it("keeps `continuedFrom` on a history row, and only when it is real", () => {
    const rows = parseConversationSummaries({
      conversations: [
        { id: "c2", title: "Moving to Brno", startedAt: 20, continuedFrom: "c1" },
        { id: "c1", title: "Moving to Brno", startedAt: 10, continuedFrom: "" },
      ],
    });
    expect(rows[0].continuedFrom).toBe("c1");
    expect(rows[1].continuedFrom).toBeUndefined();
  });
});

// ---------------------------------------------------------------------------
// The hook holds it for the thread — and a new chat clears it.
// ---------------------------------------------------------------------------
function hookArgs() {
  return {
    connected: true,
    controlsBusy: false,
    resetTransientState: vi.fn(),
    setMessages: vi.fn(),
    setActivities: vi.fn(),
    setScreen: vi.fn(),
    setStatusBanner: vi.fn(),
  } as unknown as Parameters<typeof useConversations>[0];
}

describe("opening a continued chat", () => {
  it("exposes the stored boundary, and drops it on the next ordinary chat", async () => {
    const loadConversation = ipc.loadConversation as unknown as ReturnType<typeof vi.fn>;
    loadConversation.mockResolvedValue(
      parseLoadedConversation({
        conversationId: "c2",
        title: "Moving to Brno",
        messages: [{ id: "m1", role: "user", content: "Where were we?" }],
        continuedFrom: "c1",
        summary: SUMMARY,
      }),
    );
    const { result } = renderHook(() => useConversations(hookArgs()));

    result.current.handleOpenConversation("c2");
    await waitFor(() =>
      expect(result.current.continuation).toEqual({ fromId: "c1", summary: SUMMARY }),
    );

    loadConversation.mockResolvedValue(
      parseLoadedConversation({
        conversationId: "c1",
        title: "Moving to Brno",
        messages: [{ id: "m1", role: "user", content: "hello" }],
      }),
    );
    result.current.handleOpenConversation("c1");
    await waitFor(() => expect(result.current.continuation).toBeNull());
  });
});

// ---------------------------------------------------------------------------
// The marker itself.
// ---------------------------------------------------------------------------
function renderThread(extra: Partial<React.ComponentProps<typeof ChatThread>>) {
  return render(
    <ChatThread
      messages={MESSAGES}
      onRetry={() => {}}
      retryAvailable={false}
      onRewindTo={() => {}}
      {...extra}
    />,
  );
}

describe("the boundary marker in the thread", () => {
  it("says the chat continues an earlier one, and that nothing was deleted", () => {
    renderThread({ continuation: { fromId: "c1", summary: SUMMARY } });
    const marker = screen.getByTestId("continuation-marker");
    expect(marker.textContent).toContain("Continued from an earlier chat");
    expect(marker.textContent).toContain("Nothing was deleted");
    expect(marker.textContent).toContain("still in your history");
  });

  it("sits above the first message, because that is where the boundary is", () => {
    const { container } = renderThread({ continuation: { fromId: "c1", summary: SUMMARY } });
    const marker = screen.getByTestId("continuation-marker");
    const firstMessage = container.querySelector("[data-msg-text]");
    expect(firstMessage).not.toBeNull();
    // DOCUMENT_POSITION_FOLLOWING: the message comes after the marker.
    expect(marker.compareDocumentPosition(firstMessage!) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();
  });

  it("keeps the summary behind a disclosure", () => {
    renderThread({ continuation: { fromId: "c1", summary: SUMMARY } });
    const details = screen
      .getByTestId("continuation-marker")
      .querySelector("details") as HTMLDetailsElement;
    expect(details).not.toBeNull();
    expect(details.open).toBe(false);
    expect(details.textContent).toContain("The summary Addison carried over");
    expect(details.textContent).toContain("planning a move to Brno");
  });

  it("still marks the boundary when there is no summary to show", () => {
    renderThread({ continuation: { fromId: "c1", summary: null } });
    const marker = screen.getByTestId("continuation-marker");
    expect(marker.textContent).toContain("Continued from an earlier chat");
    expect(marker.querySelector("details")).toBeNull();
  });

  it("is absent from an ordinary chat", () => {
    renderThread({});
    expect(screen.queryByTestId("continuation-marker")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// The sidebar: one chat, one entry, nothing hidden.
// ---------------------------------------------------------------------------
const NEWER: ConversationSummary = {
  id: "c2",
  title: "Moving to Brno",
  startedAt: 0,
  continuedFrom: "c1",
};
const OLDER: ConversationSummary = { id: "c1", title: "Moving to Brno", startedAt: 0 };

function renderSidebar(
  conversations: ConversationSummary[],
  onOpen = vi.fn(),
) {
  render(
    <Sidebar
      conversations={conversations}
      currentConversationId={null}
      onOpenConversation={onOpen}
      onRenameConversation={vi.fn()}
      onNewChat={vi.fn()}
      newChatDisabled={false}
      view="chat"
      onOpenSettings={vi.fn()}
      profileLabel="Simple profile"
    />,
  );
  return onOpen;
}

describe("a continued chat in the history list", () => {
  it("counts the pair as one chat", () => {
    renderSidebar([NEWER, OLDER]);
    expect(screen.getByText("1 chat")).toBeTruthy();
  });

  it("keeps both conversations listed and openable", () => {
    const onOpen = renderSidebar([NEWER, OLDER]);
    fireEvent.click(screen.getByRole("button", { name: "Moving to Brno" }));
    expect(onOpen).toHaveBeenCalledWith("c2");
    fireEvent.click(
      screen.getByRole("button", { name: "Moving to Brno — earlier part of this chat" }),
    );
    expect(onOpen).toHaveBeenCalledWith("c1");
  });

  it("says which row is the earlier part, in words and in the mono fact", () => {
    renderSidebar([NEWER, OLDER]);
    const older = screen.getByRole("button", {
      name: "Moving to Brno — earlier part of this chat",
    });
    expect(older.textContent).toContain("earlier");
  });

  it("leaves two unrelated chats as two rows", () => {
    renderSidebar([
      { id: "c2", title: "Second", startedAt: 0 },
      { id: "c1", title: "First", startedAt: 0 },
    ]);
    expect(screen.getByText("2 chats")).toBeTruthy();
  });
});

describe("lineageEntries", () => {
  it("walks a chain of three into one entry, newest first", () => {
    const entries = lineageEntries([
      { id: "c3", title: "Chat", startedAt: 30, continuedFrom: "c2" },
      { id: "c2", title: "Chat", startedAt: 20, continuedFrom: "c1" },
      { id: "c1", title: "Chat", startedAt: 10 },
    ]);
    expect(entries).toHaveLength(1);
    expect(entries[0].conversation.id).toBe("c3");
    expect(entries[0].earlier.map((c) => c.id)).toEqual(["c2", "c1"]);
  });

  it("ignores a lineage pointing at a conversation that is not in the list", () => {
    // The older half can be missing from a list the core filtered (a chat with no
    // messages, say). A pointer to nothing is not a group.
    const entries = lineageEntries([
      { id: "c2", title: "Chat", startedAt: 20, continuedFrom: "gone" },
    ]);
    expect(entries).toHaveLength(1);
    expect(entries[0].earlier).toEqual([]);
  });

  it("never loses a conversation, even to a lineage that points in a circle", () => {
    // Impossible from the core, and the one failure this list may not have: a
    // chat that is not drawn is a chat the person cannot open.
    const entries = lineageEntries([
      { id: "a", title: "A", startedAt: 20, continuedFrom: "b" },
      { id: "b", title: "B", startedAt: 10, continuedFrom: "a" },
    ]);
    const drawn = entries.flatMap((e) => [e.conversation.id, ...e.earlier.map((c) => c.id)]);
    expect([...drawn].sort()).toEqual(["a", "b"]);
  });
});
