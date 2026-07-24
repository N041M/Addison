// Routing strategies + the companion toggle + the free-model chip (Phase-2
// step 3, contract D5/D7/D8). Five parts:
//
//   (a) parseRouting fails CLOSED — off-vocabulary strategy → quality_first,
//       "balanced" and other junk dropped from availableStrategies, an unknown
//       surface → the Simple toggle (never the full picker).
//   (b) parseAnsweredWith fails CLOSED — a malformed block → undefined (no chip),
//       and free/routed are trusted only on strict `true`.
//   (c) The Simple toggle: exactly TWO options, frozen copy byte-for-byte, mapped
//       to quality_first / cost_first.
//   (d) The full picker: all FOUR strategies and never "balanced"; the custom
//       chain builder round-trips an ordered list; a refused save shows plainly.
//   (e) The free-model chip in the transcript: rendered ONLY when
//       free && routed (all four boolean combos), frozen copy byte-for-byte.

import { describe, it, expect, vi, afterEach, beforeAll } from "vitest";
import { render, screen, fireEvent, cleanup, within } from "@testing-library/react";
import { parseRouting, parseAnsweredWith } from "../ipc/client";
import { RoutingCard, type RoutingCardModel } from "../components/RoutingCard";
import { ChatThread } from "../components/ChatThread";
import type { RoutingCardState } from "../hooks/useRouting";
import type { RoutingState, DisplayMessage, AnsweredWith } from "../types/ui";

// globals:false → testing-library's automatic cleanup isn't registered.
afterEach(cleanup);

// jsdom doesn't implement scrollIntoView, which ChatThread calls after paint.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

// --- Frozen copy (contract D8) — byte-for-byte. -----------------------------
const PREFER_QUALITY = "Prefer quality — the strongest model answers.";
const PREFER_FREE = "Prefer free — free models answer when they can.";
const CHIP = "Answered with a free model.";
const STRATEGY_LABELS = ["Quality first", "Cost first", "Local only", "Custom order"];

// ---------------------------------------------------------------------------
// (a) parseRouting
// ---------------------------------------------------------------------------
describe("parseRouting", () => {
  it("round-trips a realistic routing.get payload", () => {
    expect(
      parseRouting({
        strategy: "cost_first",
        availableStrategies: ["quality_first", "cost_first", "local_only", "custom"],
        customChain: ["m-a", "m-b"],
        surface: "full",
      }),
    ).toEqual({
      strategy: "cost_first",
      availableStrategies: ["quality_first", "cost_first", "local_only", "custom"],
      customChain: ["m-a", "m-b"],
      surface: "full",
    });
  });

  it("coerces an off-vocabulary strategy to quality_first, never trusting it", () => {
    expect(parseRouting({ strategy: "balanced", surface: "full" }).strategy).toBe("quality_first");
    expect(parseRouting({ strategy: "whatever" }).strategy).toBe("quality_first");
  });

  it("drops unknown strategies (incl. balanced) from availableStrategies", () => {
    const parsed = parseRouting({
      strategy: "quality_first",
      availableStrategies: ["quality_first", "balanced", "nonsense", "custom"],
    });
    expect(parsed.availableStrategies).toEqual(["quality_first", "custom"]);
  });

  it("falls back to the toggle surface on an unknown surface value", () => {
    expect(parseRouting({ surface: "wizard" }).surface).toBe("toggle");
    expect(parseRouting({}).surface).toBe("toggle");
    expect(parseRouting({ surface: "full" }).surface).toBe("full");
  });

  it("degrades on junk instead of throwing", () => {
    for (const junk of [null, undefined, 42, "nope", []]) {
      expect(parseRouting(junk)).toEqual({
        strategy: "quality_first",
        availableStrategies: ["quality_first"],
        customChain: [],
        surface: "toggle",
      });
    }
  });

  it("keeps only string entries in a custom chain", () => {
    expect(parseRouting({ customChain: ["a", 3, null, "b", ""] }).customChain).toEqual(["a", "b"]);
  });
});

// ---------------------------------------------------------------------------
// (b) parseAnsweredWith
// ---------------------------------------------------------------------------
describe("parseAnsweredWith", () => {
  it("reads a well-formed block", () => {
    expect(
      parseAnsweredWith({ answeredWith: { modelId: "m-x", label: "Free Model", free: true, routed: true } }),
    ).toEqual({ modelId: "m-x", label: "Free Model", free: true, routed: true });
  });

  it("returns undefined when the block is absent or unusable", () => {
    expect(parseAnsweredWith({})).toBeUndefined();
    expect(parseAnsweredWith({ answeredWith: {} })).toBeUndefined();
    expect(parseAnsweredWith({ answeredWith: { modelId: "" } })).toBeUndefined();
    expect(parseAnsweredWith(null)).toBeUndefined();
  });

  it("trusts free/routed only on a strict boolean true", () => {
    const parsed = parseAnsweredWith({
      answeredWith: { modelId: "m-x", free: 1, routed: "yes" },
    });
    expect(parsed?.free).toBe(false);
    expect(parsed?.routed).toBe(false);
    // Missing label falls back to the model id.
    expect(parsed?.label).toBe("m-x");
  });
});

// ---------------------------------------------------------------------------
// RoutingCard harness
// ---------------------------------------------------------------------------
const MODELS: RoutingCardModel[] = [
  { id: "m-a", label: "Model A" },
  { id: "m-b", label: "Model B" },
  { id: "m-c", label: "Model C" },
];

function routingStateWith(
  routing: RoutingState | null,
  over: Partial<RoutingCardState> = {},
): RoutingCardState {
  return {
    routing,
    routingLoaded: true,
    busy: false,
    error: null,
    refreshRouting: vi.fn(),
    handleSetStrategy: vi.fn(async () => {}),
    handleSaveChain: vi.fn(async () => {}),
    ...over,
  };
}

function renderCard(routing: RoutingState | null, over: Partial<RoutingCardState> = {}) {
  const state = routingStateWith(routing, over);
  render(<RoutingCard connected={true} routing={state} models={MODELS} />);
  return state;
}

const TOGGLE_STATE: RoutingState = {
  strategy: "quality_first",
  availableStrategies: ["quality_first", "cost_first"],
  customChain: [],
  surface: "toggle",
};

const FULL_STATE: RoutingState = {
  strategy: "quality_first",
  availableStrategies: ["quality_first", "cost_first", "local_only", "custom"],
  customChain: [],
  surface: "full",
};

// ---------------------------------------------------------------------------
// (c) the Simple toggle surface
// ---------------------------------------------------------------------------
describe("the routing toggle (Simple)", () => {
  it("renders exactly two options with the frozen copy, byte-for-byte", () => {
    renderCard(TOGGLE_STATE);
    const group = screen.getByRole("group", { name: "How Addison picks a model" });
    expect(within(group).getAllByRole("button")).toHaveLength(2);
    expect(screen.getByText(PREFER_QUALITY)).toBeTruthy();
    expect(screen.getByText(PREFER_FREE)).toBeTruthy();
  });

  it("shows no full-picker strategy labels and no jargon", () => {
    renderCard(TOGGLE_STATE);
    expect(screen.queryByText("Local only")).toBeNull();
    expect(screen.queryByText("Custom order")).toBeNull();
    expect(screen.queryByText("Add a model")).toBeNull();
  });

  it("maps Prefer free → cost_first and Prefer quality → quality_first", () => {
    // From quality_first, picking Prefer free maps to cost_first.
    const s1 = renderCard(TOGGLE_STATE);
    fireEvent.click(screen.getByText(PREFER_FREE));
    expect(s1.handleSetStrategy).toHaveBeenCalledWith("cost_first");
    cleanup();

    // From cost_first, picking Prefer quality maps to quality_first.
    const s2 = renderCard({ ...TOGGLE_STATE, strategy: "cost_first" });
    fireEvent.click(screen.getByText(PREFER_QUALITY));
    expect(s2.handleSetStrategy).toHaveBeenCalledWith("quality_first");
  });

  it("marks the current strategy as the selected option", () => {
    renderCard({ ...TOGGLE_STATE, strategy: "cost_first" });
    const free = screen.getByText(PREFER_FREE).closest("button");
    const quality = screen.getByText(PREFER_QUALITY).closest("button");
    expect(free?.getAttribute("aria-pressed")).toBe("true");
    expect(quality?.getAttribute("aria-pressed")).toBe("false");
  });
});

// ---------------------------------------------------------------------------
// (d) the full picker + chain builder
// ---------------------------------------------------------------------------
describe("the routing full picker (Developer / Custom)", () => {
  it("shows all four strategies and never 'balanced'", () => {
    renderCard(FULL_STATE);
    for (const label of STRATEGY_LABELS) {
      expect(screen.getByText(label)).toBeTruthy();
    }
    expect(screen.queryByText(/balanced/i)).toBeNull();
  });

  it("picking a strategy saves it", () => {
    const state = renderCard(FULL_STATE);
    fireEvent.click(screen.getByText("Local only"));
    expect(state.handleSetStrategy).toHaveBeenCalledWith("local_only");
  });

  it("shows the chain builder only for the Custom-order strategy", () => {
    renderCard(FULL_STATE);
    // quality_first selected → no builder.
    expect(screen.queryByText("Add a model")).toBeNull();
    cleanup();
    renderCard({ ...FULL_STATE, strategy: "custom", customChain: ["m-a"] });
    expect(screen.getByText("Add a model")).toBeTruthy();
  });

  it("round-trips an edited ordered chain to handleSaveChain", () => {
    const state = renderCard({ ...FULL_STATE, strategy: "custom", customChain: ["m-a", "m-b"] });
    // Reorder: move Model A down, so the order becomes [m-b, m-a].
    fireEvent.click(screen.getByRole("button", { name: "Move Model A down" }));
    fireEvent.click(screen.getByRole("button", { name: "Save order" }));
    expect(state.handleSaveChain).toHaveBeenCalledWith(["m-b", "m-a"]);
  });

  it("adds and removes chain members, saving the full list", () => {
    const state = renderCard({ ...FULL_STATE, strategy: "custom", customChain: ["m-a"] });
    // Add Model C via the select.
    fireEvent.change(screen.getByLabelText("Add a model"), { target: { value: "m-c" } });
    // Remove Model A.
    fireEvent.click(screen.getByRole("button", { name: "Remove Model A" }));
    fireEvent.click(screen.getByRole("button", { name: "Save order" }));
    expect(state.handleSaveChain).toHaveBeenCalledWith(["m-c"]);
  });

  it("renders a refused save as one plain sentence, not a stack trace", () => {
    const refusal =
      "Addison couldn't save the restore point that goes with changing the model order, " +
      "so nothing was changed. Try again in a moment.";
    renderCard(FULL_STATE, { error: refusal });
    const text = document.body.textContent ?? "";
    expect(text).toContain(refusal);
    expect(text).not.toContain("Traceback");
    expect(text).not.toContain("Error:");
  });
});

// ---------------------------------------------------------------------------
// The card's load/connect guards
// ---------------------------------------------------------------------------
describe("the routing card's guards", () => {
  it("shows a quiet line before routing has loaded", () => {
    render(
      <RoutingCard
        connected={true}
        routing={routingStateWith(null, { routingLoaded: false })}
        models={MODELS}
      />,
    );
    expect(screen.getByText("Loading your settings…")).toBeTruthy();
    expect(screen.queryByText(PREFER_QUALITY)).toBeNull();
  });

  it("shows a not-connected line when the engine is offline", () => {
    render(<RoutingCard connected={false} routing={routingStateWith(TOGGLE_STATE)} models={MODELS} />);
    expect(screen.getByText("This appears here once Addison’s engine is connected.")).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// (e) the free-model chip in the transcript
// ---------------------------------------------------------------------------
function answered(over: Partial<AnsweredWith>): AnsweredWith {
  return { modelId: "m-free", label: "A Free Model", free: false, routed: false, ...over };
}

function renderThreadWith(answeredWith: AnsweredWith | undefined) {
  const messages: DisplayMessage[] = [
    { id: "a1", role: "assistant", content: "Here is your answer.", answeredWith },
  ];
  render(
    <ChatThread messages={messages} onRetry={vi.fn()} retryAvailable={false} onRewindTo={vi.fn()} />,
  );
}

describe("the free-model chip", () => {
  it("renders the frozen copy when free && routed — byte-for-byte", () => {
    renderThreadWith(answered({ free: true, routed: true }));
    expect(screen.getByText(CHIP)).toBeTruthy();
  });

  it("does NOT render for the other three boolean combinations", () => {
    for (const [free, routed] of [
      [false, false],
      [true, false],
      [false, true],
    ] as const) {
      renderThreadWith(answered({ free, routed }));
      expect(screen.queryByText(CHIP)).toBeNull();
      cleanup();
    }
  });

  it("does NOT render when there is no answeredWith block at all", () => {
    renderThreadWith(undefined);
    expect(screen.queryByText(CHIP)).toBeNull();
  });
});
