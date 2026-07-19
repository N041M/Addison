// Double-click-to-rename in the sidebar (behavioral, via the real Sidebar).
// The disconnected browser preview never shows conversation rows, so this is
// where the inline-edit flow is actually exercised: double-click swaps the
// title button for an input, Enter commits with the new value, Escape cancels.
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
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
      screen="chat"
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
