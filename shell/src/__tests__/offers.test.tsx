// useOffers — the trigger half of the two step-4 conversational offers (add a
// model server / make it cheaper). The core owns whether a proposal is real; this
// hook owns WHEN it is asked and WHICH card is shown, and both of those are
// safety-relevant:
//
//   * only the person's OWN words may arm a card. The core enforces the same rule
//     (rpc/providers._current_turn_user_texts reads role=="user" only); this is
//     the frontend half of that one decision, and if the trigger ever ran on the
//     model's reply, a page the model quoted could put a connect card — with a key
//     field — under the composer.
//   * one card at a time. Two consent cards stacked under the composer is how a
//     person confirms the one they did not read.
//   * a `null` proposal shows nothing at all, silently. Addison has already
//     answered in prose; a card saying "nothing to add" would be noise.

import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useOffers, WANTS_CHEAPER, WANTS_ENDPOINT } from "../hooks/useOffers";
import { ipc } from "../ipc/client";

vi.mock("../ipc/client", () => ({
  ipc: {
    proposeEndpoint: vi.fn(),
    proposeCostPlan: vi.fn(),
    confirmAddEndpoint: vi.fn(async () => ({ ok: false })),
    applyCostPlan: vi.fn(async () => ({ ok: false })),
  },
}));

const proposeEndpoint = ipc.proposeEndpoint as unknown as ReturnType<typeof vi.fn>;
const proposeCostPlan = ipc.proposeCostPlan as unknown as ReturnType<typeof vi.fn>;
const confirmAddEndpoint = ipc.confirmAddEndpoint as unknown as ReturnType<typeof vi.fn>;
const applyCostPlan = ipc.applyCostPlan as unknown as ReturnType<typeof vi.fn>;

const PROPOSAL = { baseUrl: "http://192.168.1.5:11434", isLocalOrLan: true };
const PLAN = { skillName: "Addison: keep it cheap", skillInstructions: "Keep answers short." };

function makeHook(connected = true) {
  const setStatusBanner = vi.fn();
  const refresh = vi.fn();
  const hook = renderHook(() => useOffers(() => connected, setStatusBanner, refresh));
  return { hook, setStatusBanner, refresh };
}

async function flush() {
  await Promise.resolve();
  await Promise.resolve();
}

beforeEach(() => {
  proposeEndpoint.mockReset().mockResolvedValue(PROPOSAL);
  proposeCostPlan.mockReset().mockResolvedValue(PLAN);
  confirmAddEndpoint.mockReset().mockResolvedValue({ ok: false });
  applyCostPlan.mockReset().mockResolvedValue({ ok: false });
});

describe("the offer triggers", () => {
  it("arms the endpoint card only for an add-a-server sentence", () => {
    expect(WANTS_ENDPOINT.test("add my own model server at http://box:11434")).toBe(true);
    expect(WANTS_ENDPOINT.test("Can you connect my Ollama?")).toBe(true);
    // Not every sentence containing a URL, and not an unrelated "add".
    expect(WANTS_ENDPOINT.test("add milk to my shopping list")).toBe(false);
    expect(WANTS_ENDPOINT.test("here is a page I copied: http://example.com/v1")).toBe(false);
  });

  it("arms the cost card only for a cost sentence", () => {
    expect(WANTS_CHEAPER.test("can you make this cheaper?")).toBe(true);
    expect(WANTS_CHEAPER.test("this is getting expensive")).toBe(true);
    expect(WANTS_CHEAPER.test("what did the sculpture cost?")).toBe(true); // accepted breadth
    expect(WANTS_CHEAPER.test("tell me about ferns")).toBe(false);
  });
});

describe("useOffers", () => {
  it("asks the core, and shows the card, for an add-a-server turn", async () => {
    const { hook } = makeHook();
    await act(async () => {
      hook.result.current.maybeProposeOffers("please add my model server at http://box:11434");
      await flush();
    });
    expect(proposeEndpoint).toHaveBeenCalledTimes(1);
    expect(hook.result.current.endpointProposal).toEqual(PROPOSAL);
    expect(hook.result.current.costPlan).toBeNull();
  });

  it("shows nothing, silently, when the core has nothing to propose", async () => {
    proposeEndpoint.mockResolvedValue(null);
    const { hook } = makeHook();
    await act(async () => {
      hook.result.current.maybeProposeOffers("add my server");
      await flush();
    });
    expect(hook.result.current.endpointProposal).toBeNull();
  });

  it("stays quiet when the core refuses outright", async () => {
    proposeEndpoint.mockRejectedValue(new Error("nope"));
    const { hook } = makeHook();
    await act(async () => {
      hook.result.current.maybeProposeOffers("add my server");
      await flush();
    });
    expect(hook.result.current.endpointProposal).toBeNull();
  });

  it("never asks the core for an unrelated turn", async () => {
    const { hook } = makeHook();
    await act(async () => {
      hook.result.current.maybeProposeOffers("what's the weather like?");
      await flush();
    });
    expect(proposeEndpoint).not.toHaveBeenCalled();
    expect(proposeCostPlan).not.toHaveBeenCalled();
  });

  it("never asks the core while the engine is disconnected", async () => {
    const { hook } = makeHook(false);
    await act(async () => {
      hook.result.current.maybeProposeOffers("add my model server at http://box:11434");
      await flush();
    });
    expect(proposeEndpoint).not.toHaveBeenCalled();
  });

  it("shows only ONE card when a turn could match both", async () => {
    const { hook } = makeHook();
    await act(async () => {
      // "add ... server" AND "cheaper" in one sentence.
      hook.result.current.maybeProposeOffers("add a cheaper model server for me");
      await flush();
    });
    expect(hook.result.current.endpointProposal).toEqual(PROPOSAL);
    expect(hook.result.current.costPlan).toBeNull();
    expect(proposeCostPlan).not.toHaveBeenCalled();
  });

  it("dismissing tells the core to drop its held draft", async () => {
    const { hook } = makeHook();
    await act(async () => {
      hook.result.current.maybeProposeOffers("add my model server at http://box:11434");
      await flush();
    });
    await act(async () => {
      hook.result.current.dismissEndpointProposal();
      await flush();
    });
    expect(hook.result.current.endpointProposal).toBeNull();
    expect(confirmAddEndpoint).toHaveBeenCalledWith("", false);

    await act(async () => {
      hook.result.current.dismissCostPlan();
      await flush();
    });
    expect(applyCostPlan).toHaveBeenCalledWith(false);
  });

  it("announces and refreshes after each apply", async () => {
    const { hook, setStatusBanner, refresh } = makeHook();
    act(() => hook.result.current.handleEndpointAdded());
    expect(setStatusBanner).toHaveBeenCalledWith(
      "Added your server — it's in Settings under your services.",
    );
    expect(refresh).toHaveBeenCalledTimes(1);

    act(() => hook.result.current.handleCostPlanApplied());
    expect(setStatusBanner).toHaveBeenLastCalledWith(
      "Addison will keep answers short and prefer cheaper models. Your previous setup is " +
        "saved as a restore point in Settings.",
    );
    expect(refresh).toHaveBeenCalledTimes(2);
  });
});
