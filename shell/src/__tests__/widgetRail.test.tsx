// The rail draws two kinds of thing that can say the same sentence: stored
// widgets the person pinned, and the core-computed rows the rail adds by itself
// (the token meter, the connection list). Nothing stopped both from showing the
// same source, so a pinned "Connections" widget put Ollama and the Anthropic API
// on screen, then the ambient block put them there again underneath. Reported
// from a running app, 2026-07-26.

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, cleanup, fireEvent } from "@testing-library/react";
import { WidgetRail } from "../components/WidgetRail";
import type { Stats, Widget } from "../types/ui";

afterEach(cleanup);

// jsdom has no layout, so no scrollIntoView; the model menu keeps its
// highlighted row in view with it.
beforeEach(() => {
  Element.prototype.scrollIntoView = () => {};
});

const STATS: Stats = {
  tokensMonth: { total: 106_000, limit: 380_000 },
  providerLatency: [{ provider: "anthropic", ms: 2629 }],
  connections: [
    { id: "ollama", label: "Ollama · this computer", status: "unreachable", detail: "not running" },
    { id: "anthropic", label: "Anthropic API", status: "reachable", detail: "2629 ms" },
  ],
};

function statWidget(id: string, source: "connections" | "tokens_month", pinned: boolean): Widget {
  return {
    id,
    pinned,
    spec: { kind: "stat", source, title: source === "connections" ? "Connections" : "Tokens" },
  } as Widget;
}

function renderRail(widgets: Widget[]) {
  return render(
    <WidgetRail
      work={null}
      consent={null}
      developer={false}
      widgets={widgets}
      stats={STATS}
      routines={[]}
      onSetPinned={vi.fn()}
      onDelete={vi.fn()}
      onRunRoutine={vi.fn()}
      onRunCommandWidget={vi.fn()}
      onAskBuildWidget={vi.fn()}
    />,
  );
}

/** How many times a connection's label is on screen. */
const connectionRows = () => screen.queryAllByText("Ollama · this computer").length;

describe("the widget rail", () => {
  it("shows the connections once when a pinned widget already shows them", () => {
    renderRail([statWidget("w1", "connections", true)]);
    expect(connectionRows()).toBe(1);
  });

  it("still shows the ambient connections when nothing is pinned for them", () => {
    renderRail([]);
    expect(connectionRows()).toBe(1);
  });

  it("still shows them when the only connections widget sits in the closed tray", () => {
    // An unpinned widget is behind the "1 more widget" row, so it is not on
    // screen — the ambient row is the only place those facts appear.
    renderRail([statWidget("w1", "connections", false)]);
    expect(connectionRows()).toBe(1);
  });

  it("shows the token total once when a pinned widget already shows it", () => {
    renderRail([statWidget("w1", "tokens_month", true)]);
    expect(screen.queryAllByText(/106k/)).toHaveLength(1);
  });

  it("leaves an unrelated pinned widget alone", () => {
    // A pinned latency widget says nothing about connections, so the ambient
    // connection rows must still appear.
    const latency = { id: "w9", pinned: true, spec: { kind: "stat", source: "provider_latency" } } as Widget;
    renderRail([latency]);
    expect(connectionRows()).toBe(1);
  });
});

// ---------------------------------------------------------------------------
// The menus fade out instead of vanishing on a frame (reported 2026-07-26).
// The element stays painted for the exit, but leaves the accessibility tree
// straight away — a closing menu is a picture, not a control.
// ---------------------------------------------------------------------------
describe("the model menus close with an animation", () => {
  it("keeps the composer menu painted while it fades, and out of the a11y tree", async () => {
    const { ModelSelector } = await import("../components/ModelSelector");
    const cloudModels = [
      { id: "opus", label: "Claude Opus 4.8", effortLevels: [], default: true },
    ] as never[];
    const { container } = render(
      <ModelSelector
        roles={[]}
        cloudModels={cloudModels}
        selectedRole="primary"
        selectedCloudModel="opus"
        onSelectModel={vi.fn()}
        onSelectEffort={vi.fn()}
      />,
    );
    const trigger = screen.getByRole("button", { name: /^Model:/ });
    fireEvent.click(trigger);
    expect(screen.getByRole("tree")).toBeTruthy();

    fireEvent.click(trigger); // close

    // Gone from the a11y tree at once...
    expect(screen.queryByRole("tree")).toBeNull();
    // ...but still painted, carrying the exit animation, so it is not a plop.
    const panel = container.querySelector('[aria-hidden="true"][role="presentation"]');
    expect(panel, "the closing panel should still be painted").toBeTruthy();
    expect(panel!.className).toContain("fadeDrop");
    expect(panel!.className).toContain("pointer-events-none");
  });
});

// ---------------------------------------------------------------------------
// Reported again from a running app, 2026-07-27, with a screenshot: the rail
// showed "Connections / Ollama / Anthropic API", the token meter, and then
// Ollama and the Anthropic API a SECOND time. The 07-26 fix suppresses the
// ambient rows for any source a pinned widget shows, so that collision is
// closed — which means the surviving duplicate is one it never considered:
// TWO pinned widgets on the same source. Nothing dedupes widget against widget.
//
// `ConnectionsBody` renders its title conditionally, so a second connections
// widget saved without a title draws exactly the bare dotted rows in the
// screenshot, with nothing to distinguish it from the ambient block.
// ---------------------------------------------------------------------------
describe("two pinned widgets on the same source", () => {
  it("does not draw the same connection rows twice", () => {
    renderRail([
      statWidget("w1", "connections", true),
      { id: "w2", pinned: true, spec: { kind: "stat", source: "connections" } } as Widget,
    ]);
    // One row per connection, however many widgets claim that source.
    expect(screen.getAllByText("Anthropic API")).toHaveLength(1);
    expect(screen.getAllByText("Ollama · this computer")).toHaveLength(1);
  });

  it("does not draw the token meter twice", () => {
    renderRail([
      statWidget("t1", "tokens_month", true),
      { id: "t2", pinned: true, spec: { kind: "stat", source: "tokens_month" } } as Widget,
    ]);
    // The meter renders "106k / 380k" when a limit is known, so match the pair.
    expect(screen.getAllByText("106k / 380k")).toHaveLength(1);
  });

  // The 07-26 fix suppressed ambient rows for PINNED widgets only, reasoning that
  // "a widget sitting in the collapsed tray is not on screen, so there the ambient
  // row is the only place those facts appear." That is right — while the tray is
  // CLOSED. Open it and the tray widget is on screen too, so the ambient row
  // becomes the second copy. The rule was correct; its condition was `pinned`
  // where it needed to be "pinned, or visible in an open tray".
  it("stands the ambient rows down when an OPEN tray already shows that source", () => {
    renderRail([statWidget("w1", "connections", false)]);
    fireEvent.click(screen.getByRole("button", { name: /1 more widget/i }));
    expect(screen.getAllByText("Anthropic API")).toHaveLength(1);
  });
});
