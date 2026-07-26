// Double-click-to-rename in the sidebar (behavioral, via the real Sidebar).
// The disconnected browser preview never shows conversation rows, so this is
// where the inline-edit flow is actually exercised: double-click swaps the
// title button for an input, Enter commits with the new value, Escape cancels.
//
// The 2026-07-25 dark redesign restyled the sidebar and renamed its `screen`
// prop to `view` (five views now, not two). The rename flow itself is unchanged
// — which is the point of this suite, so the assertions below are the same
// behaviour, driven through the new props. The row's accessible name is now set
// explicitly to the chat's title, because the visible row also carries a mono
// timestamp that is a machine fact rather than part of what the row is called.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup, act } from "@testing-library/react";
import { Sidebar } from "../components/Sidebar";
import type { ConversationSummary } from "../types/ui";

// globals:false → testing-library's automatic afterEach cleanup isn't
// registered, so unmount between tests explicitly (else rows accumulate).
afterEach(cleanup);

const CONVO: ConversationSummary = {
  id: "c1",
  title: "First chat",
  startedAt: 0,
  messageCount: 2,
};

function renderSidebar(onRename = vi.fn()) {
  render(
    <Sidebar
      conversations={[CONVO]}
      currentConversationId={null}
      onOpenConversation={vi.fn()}
      onRenameConversation={onRename}
      onNewChat={vi.fn()}
      newChatDisabled={false}
      view="chat"
      onOpenSettings={vi.fn()}
      profileLabel="Simple profile"
    />,
  );
  return onRename;
}

describe("sidebar chat rename", () => {
  it("double-click opens an inline editor prefilled with the title", () => {
    renderSidebar();
    fireEvent.doubleClick(screen.getByRole("button", { name: "First chat" }));
    const input = screen.getByRole("textbox", { name: "Rename chat" }) as HTMLInputElement;
    expect(input.value).toBe("First chat");
  });

  it("Enter commits the new name", () => {
    const onRename = renderSidebar();
    fireEvent.doubleClick(screen.getByRole("button", { name: "First chat" }));
    const input = screen.getByRole("textbox", { name: "Rename chat" });
    fireEvent.change(input, { target: { value: "Renamed" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onRename).toHaveBeenCalledWith("c1", "Renamed");
  });

  it("Escape cancels without renaming and restores the title", () => {
    const onRename = renderSidebar();
    fireEvent.doubleClick(screen.getByRole("button", { name: "First chat" }));
    const input = screen.getByRole("textbox", { name: "Rename chat" });
    fireEvent.change(input, { target: { value: "Discarded" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onRename).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "First chat" })).toBeTruthy();
  });
});

// A conversation with no usable timestamp must not be dated with a guess: it
// falls under "Earlier" and shows no time at all. (The fixture above carries
// startedAt: 0 — the case a real `conversation.list` row hits when the core
// couldn't tell us when the chat began.)
describe("sidebar chat grouping", () => {
  it("puts an undated conversation under Earlier, with no invented time", () => {
    renderSidebar();
    expect(screen.getByText("Earlier")).toBeTruthy();
    expect(screen.queryByText("Today")).toBeNull();
    const row = screen.getByRole("button", { name: "First chat" });
    expect(row.textContent).toBe("First chat");
  });
});

// Group expand/collapse (dark redesign §4). The animation is the design here, so
// the machinery is refs + timers; what these pin is the STATE it commits, which
// is where a faithful port can quietly go wrong: a collapse must commit exactly
// once, after its exit animation, and a re-click mid-collapse must cancel it
// outright rather than queue a second commit that lands after the person changed
// their mind.
function renderMany(count: number) {
  const rows: ConversationSummary[] = Array.from({ length: count }, (_, i) => ({
    id: `c${i}`,
    title: `Chat ${i}`,
    startedAt: 0,
  }));
  render(
    <Sidebar
      conversations={rows}
      currentConversationId={null}
      onOpenConversation={vi.fn()}
      onRenameConversation={vi.fn()}
      onNewChat={vi.fn()}
      newChatDisabled={false}
      view="chat"
      onOpenSettings={vi.fn()}
      profileLabel="Simple profile"
    />,
  );
}

describe("sidebar group expand and collapse", () => {
  afterEach(() => vi.useRealTimers());

  it("shows three rows and an honest count of the rest", () => {
    renderMany(5);
    expect(screen.getByRole("button", { name: "Chat 0" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Chat 2" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Chat 3" })).toBeNull();
    expect(screen.getByText("2 more…")).toBeTruthy();
    expect(screen.getByText("5 chats")).toBeTruthy();
  });

  it("expands to every row, and the hint offers the way back", () => {
    renderMany(5);
    fireEvent.click(screen.getByText("2 more…"));
    expect(screen.getByRole("button", { name: "Chat 4" })).toBeTruthy();
    expect(screen.queryByText("2 more…")).toBeNull();
    expect(screen.getByText("collapse")).toBeTruthy();
  });

  it("commits a collapse only after its exit animation", () => {
    vi.useFakeTimers();
    renderMany(5);
    fireEvent.click(screen.getByText("2 more…"));
    expect(screen.getByRole("button", { name: "Chat 4" })).toBeTruthy();

    fireEvent.click(screen.getByText("collapse"));
    // Still there while the rows are leaving — the state has not committed yet.
    expect(screen.getByRole("button", { name: "Chat 4" })).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(300);
    });
    expect(screen.queryByRole("button", { name: "Chat 4" })).toBeNull();
    expect(screen.getByText("2 more…")).toBeTruthy();
  });

  it("a re-click mid-collapse cancels it, and no stale commit lands after", () => {
    vi.useFakeTimers();
    renderMany(5);
    fireEvent.click(screen.getByText("2 more…"));

    fireEvent.click(screen.getByText("collapse"));
    act(() => {
      vi.advanceTimersByTime(120); // mid-flight
    });
    fireEvent.click(screen.getByText("collapse")); // "no, keep them"

    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(screen.getByRole("button", { name: "Chat 4" })).toBeTruthy();
    expect(screen.getByText("collapse")).toBeTruthy();
  });
});
