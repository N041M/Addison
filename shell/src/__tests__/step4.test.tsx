// Free-model endpoints (add-by-prompt) + "make it cheaper" + the Google
// free-tier line (Phase-2 step 4, contract D3/D4/D5). Five parts:
//
//   (a) parseEndpointProposal fails CLOSED — {none}, a missing/empty base URL,
//       and a non-http(s) scheme all yield null (no card); isLocalOrLan is
//       trusted only on a strict boolean true.
//   (b) parseCostPlan fails CLOSED — {none}, a missing name/instructions yields
//       null; strategy is hard-set to cost_first regardless of the wire.
//   (c) The endpoint card: frozen copy byte-for-byte, the LAN disclosure, and —
//       the hard requirement (G1) — the key goes to storeProviderKey (keychain),
//       NEVER into an endpoint.* frame; confirmAdd carries the base URL only.
//   (d) The cost-plan card: renders the skill name AND its FULL instructions,
//       the frozen summary, applies on confirm, and shows a refusal plainly.
//   (e) The Google free-tier line: the frozen sentence with a real, openable
//       link (href = the full URL) — never dead text.

import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, fireEvent, cleanup, waitFor } from "@testing-library/react";

// Keep the real parsers (they need no IPC); mock only the side-effectful calls
// the cards make — storeProviderKey (keychain, Rust) and the two ipc.* methods.
vi.mock("../ipc/client", async (importActual) => {
  const actual = await importActual<typeof import("../ipc/client")>();
  return {
    ...actual,
    storeProviderKey: vi.fn(),
    ipc: {
      ...actual.ipc,
      confirmAddEndpoint: vi.fn(),
      applyCostPlan: vi.fn(),
    },
  };
});

import {
  ipc,
  storeProviderKey,
  parseEndpointProposal,
  parseCostPlan,
} from "../ipc/client";
import { EndpointProposalCard } from "../components/EndpointProposalCard";
import { CostPlanCard } from "../components/CostPlanCard";
import { ApiKeys } from "../components/SettingsPage";
import type { EndpointProposal, CostPlan } from "../types/ui";

afterEach(cleanup);

const confirmAddEndpoint = vi.mocked(ipc.confirmAddEndpoint);
const applyCostPlan = vi.mocked(ipc.applyCostPlan);
const storeKey = vi.mocked(storeProviderKey);

beforeEach(() => {
  storeKey.mockReset().mockResolvedValue(undefined);
  confirmAddEndpoint.mockReset().mockResolvedValue({ ok: true });
  applyCostPlan.mockReset().mockResolvedValue({ ok: true });
});

// --- Frozen copy (contract D5) — byte-for-byte. -----------------------------
const ENDPOINT_TITLE = "Add a model server?";
const PROVIDER_NAME = "Your own server";
const PROVENANCE = "You asked Addison to add this address.";
const LAN = "This points to your own computer or a device on your network.";
const KEY_LABEL = "Paste the server's API key (it stays in your keychain)";
const COST_SUMMARY =
  "Addison will add this guidance note and switch model picking to prefer cheaper " +
  "models. Your current setup is saved as a restore point first — one click in " +
  "Settings puts it back.";
const COST_REFUSED =
  "Addison couldn't save the restore point that goes with this change, so nothing " +
  "was changed. Try again in a moment.";
const GOOGLE_LINE =
  "Google's API has a free tier — a free key works here. Open aistudio.google.com/apikey " +
  "in your browser to get one.";

// ---------------------------------------------------------------------------
// (a) parseEndpointProposal
// ---------------------------------------------------------------------------
describe("parseEndpointProposal", () => {
  it("reads a well-formed proposal, trusting isLocalOrLan on a strict true", () => {
    expect(
      parseEndpointProposal({ baseUrl: "https://box.example/v1", isLocalOrLan: true }),
    ).toEqual({ baseUrl: "https://box.example/v1", isLocalOrLan: true });
    expect(
      parseEndpointProposal({ baseUrl: "http://localhost:1234/v1", isLocalOrLan: false }),
    ).toEqual({ baseUrl: "http://localhost:1234/v1", isLocalOrLan: false });
  });

  it("returns null for {none}, a missing, or an empty base URL", () => {
    expect(parseEndpointProposal({ none: true })).toBeNull();
    expect(parseEndpointProposal({})).toBeNull();
    expect(parseEndpointProposal({ baseUrl: "" })).toBeNull();
    expect(parseEndpointProposal({ baseUrl: 42 })).toBeNull();
  });

  it("returns null for a non-http(s) scheme — never renders a card for it", () => {
    expect(parseEndpointProposal({ baseUrl: "ftp://box.example" })).toBeNull();
    expect(parseEndpointProposal({ baseUrl: "javascript:alert(1)" })).toBeNull();
    expect(parseEndpointProposal({ baseUrl: "box.example/v1" })).toBeNull();
  });

  it("trusts isLocalOrLan only on a strict boolean true", () => {
    expect(parseEndpointProposal({ baseUrl: "https://x", isLocalOrLan: 1 })?.isLocalOrLan).toBe(
      false,
    );
    expect(parseEndpointProposal({ baseUrl: "https://x", isLocalOrLan: "yes" })?.isLocalOrLan).toBe(
      false,
    );
    expect(parseEndpointProposal({ baseUrl: "https://x" })?.isLocalOrLan).toBe(false);
  });

  it("degrades on junk instead of throwing", () => {
    for (const junk of [null, undefined, 42, "nope", []]) {
      expect(parseEndpointProposal(junk)).toBeNull();
    }
  });
});

// ---------------------------------------------------------------------------
// (b) parseCostPlan
// ---------------------------------------------------------------------------
describe("parseCostPlan", () => {
  it("reads a well-formed plan", () => {
    expect(
      parseCostPlan({
        skillName: "Addison: keep it cheap",
        skillInstructions: "Be brief. Prefer cheaper models.",
        strategy: "cost_first",
      }),
    ).toEqual({
      skillName: "Addison: keep it cheap",
      skillInstructions: "Be brief. Prefer cheaper models.",
      strategy: "cost_first",
    });
  });

  it("hard-sets the strategy to cost_first, never trusting the wire", () => {
    const parsed = parseCostPlan({
      skillName: "n",
      skillInstructions: "i",
      strategy: "quality_first",
    });
    expect(parsed?.strategy).toBe("cost_first");
  });

  it("returns null for {none} or a plan missing its name or instructions", () => {
    expect(parseCostPlan({ none: true })).toBeNull();
    expect(parseCostPlan({ skillName: "n" })).toBeNull();
    expect(parseCostPlan({ skillInstructions: "i" })).toBeNull();
    expect(parseCostPlan({ skillName: "", skillInstructions: "i" })).toBeNull();
    expect(parseCostPlan({ skillName: "n", skillInstructions: "" })).toBeNull();
  });

  it("degrades on junk instead of throwing", () => {
    for (const junk of [null, undefined, 42, "nope", []]) {
      expect(parseCostPlan(junk)).toBeNull();
    }
  });
});

// ---------------------------------------------------------------------------
// (c) the endpoint card
// ---------------------------------------------------------------------------
const PROPOSAL: EndpointProposal = { baseUrl: "https://box.example/v1", isLocalOrLan: false };

function renderEndpoint(
  over: Partial<EndpointProposal> = {},
  handlers: { onDismiss?: () => void; onAdded?: () => void } = {},
) {
  const onDismiss = handlers.onDismiss ?? vi.fn();
  const onAdded = handlers.onAdded ?? vi.fn();
  render(
    <EndpointProposalCard proposal={{ ...PROPOSAL, ...over }} onDismiss={onDismiss} onAdded={onAdded} />,
  );
  return { onDismiss, onAdded };
}

describe("the endpoint card", () => {
  it("shows the frozen copy byte-for-byte, with the full base URL and no model label", () => {
    renderEndpoint();
    expect(screen.getByText(ENDPOINT_TITLE)).toBeTruthy();
    expect(screen.getByText("https://box.example/v1")).toBeTruthy();
    expect(screen.getByText(PROVIDER_NAME)).toBeTruthy();
    expect(screen.getByText(PROVENANCE)).toBeTruthy();
    expect(screen.getByText(KEY_LABEL)).toBeTruthy();
    expect(screen.getByText("Add server")).toBeTruthy();
    expect(screen.getByText("Not now")).toBeTruthy();
  });

  it("appends the LAN disclosure only when isLocalOrLan is true", () => {
    renderEndpoint({ isLocalOrLan: false });
    expect(screen.queryByText(LAN)).toBeNull();
    cleanup();
    renderEndpoint({ isLocalOrLan: true });
    expect(screen.getByText(LAN)).toBeTruthy();
  });

  it("G1 — the key goes to storeProviderKey (keychain), NEVER an endpoint frame", async () => {
    const { onDismiss, onAdded } = renderEndpoint();
    fireEvent.change(screen.getByLabelText(KEY_LABEL), { target: { value: "sk-secret-123" } });
    fireEvent.click(screen.getByText("Add server"));

    await waitFor(() => expect(onDismiss).toHaveBeenCalled());

    // The key went to the keychain path, under the custom provider id.
    expect(storeKey).toHaveBeenCalledWith("custom", "sk-secret-123");
    // confirmAdd carried the base URL + the decision — and NOT the key.
    expect(confirmAddEndpoint).toHaveBeenCalledWith("https://box.example/v1", true);
    const confirmArgs = JSON.stringify(confirmAddEndpoint.mock.calls);
    expect(confirmArgs).not.toContain("sk-secret-123");
    // Keychain first, then the connect — the key is stored before anything connects.
    expect(storeKey.mock.invocationCallOrder[0]).toBeLessThan(
      confirmAddEndpoint.mock.invocationCallOrder[0],
    );
    expect(onAdded).toHaveBeenCalled();
  });

  it("adds a keyless server (custom key is optional) without touching the keychain", async () => {
    const { onDismiss } = renderEndpoint();
    fireEvent.click(screen.getByText("Add server"));
    await waitFor(() => expect(onDismiss).toHaveBeenCalled());
    expect(storeKey).not.toHaveBeenCalled();
    expect(confirmAddEndpoint).toHaveBeenCalledWith("https://box.example/v1", true);
  });

  it("declining drops the held draft (accept:false) and never stores a key", () => {
    const { onDismiss } = renderEndpoint();
    fireEvent.change(screen.getByLabelText(KEY_LABEL), { target: { value: "sk-secret-123" } });
    fireEvent.click(screen.getByText("Not now"));
    expect(confirmAddEndpoint).toHaveBeenCalledWith("https://box.example/v1", false);
    expect(storeKey).not.toHaveBeenCalled();
    expect(onDismiss).toHaveBeenCalled();
  });

  it("shows a refused connect as one plain sentence and keeps the card open", async () => {
    confirmAddEndpoint.mockResolvedValue({ ok: false, error: "Couldn't reach that server." });
    const { onDismiss } = renderEndpoint();
    fireEvent.click(screen.getByText("Add server"));
    expect(await screen.findByText("Couldn't reach that server.")).toBeTruthy();
    expect(onDismiss).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// (d) the cost-plan card
// ---------------------------------------------------------------------------
const PLAN: CostPlan = {
  skillName: "Addison: keep it cheap",
  skillInstructions:
    "Keep answers brief and to the point.\nPrefer cheaper and free models whenever they can do the job.",
  strategy: "cost_first",
};

function renderCostPlan(handlers: { onDismiss?: () => void; onApplied?: () => void } = {}) {
  const onDismiss = handlers.onDismiss ?? vi.fn();
  const onApplied = handlers.onApplied ?? vi.fn();
  render(<CostPlanCard plan={PLAN} onDismiss={onDismiss} onApplied={onApplied} />);
  return { onDismiss, onApplied };
}

describe("the cost-plan card", () => {
  it("renders the skill NAME and its FULL instructions, plus the frozen summary", () => {
    renderCostPlan();
    expect(screen.getByText("Addison: keep it cheap")).toBeTruthy();
    // The full instructions text is present, not a truncation.
    const body = document.body.textContent ?? "";
    expect(body).toContain("Keep answers brief and to the point.");
    expect(body).toContain("Prefer cheaper and free models whenever they can do the job.");
    expect(screen.getByText(COST_SUMMARY)).toBeTruthy();
  });

  it("applies on confirm, then dismisses", async () => {
    const { onDismiss, onApplied } = renderCostPlan();
    fireEvent.click(screen.getByText("Make it cheaper"));
    await waitFor(() => expect(onDismiss).toHaveBeenCalled());
    expect(applyCostPlan).toHaveBeenCalledWith(true);
    expect(onApplied).toHaveBeenCalled();
  });

  it("declining drops the held plan (accept:false)", () => {
    const { onDismiss } = renderCostPlan();
    fireEvent.click(screen.getByText("Not now"));
    expect(applyCostPlan).toHaveBeenCalledWith(false);
    expect(onDismiss).toHaveBeenCalled();
  });

  it("renders a refusal as one plain sentence, not a stack trace, keeping the card open", async () => {
    applyCostPlan.mockResolvedValue({ ok: false, error: COST_REFUSED });
    const { onDismiss } = renderCostPlan();
    fireEvent.click(screen.getByText("Make it cheaper"));
    expect(await screen.findByText(COST_REFUSED)).toBeTruthy();
    const body = document.body.textContent ?? "";
    expect(body).not.toContain("Traceback");
    expect(body).not.toContain("Error:");
    expect(onDismiss).not.toHaveBeenCalled();
  });

  it("falls back to the frozen refusal sentence when the core sends no error text", async () => {
    applyCostPlan.mockResolvedValue({ ok: false });
    renderCostPlan();
    fireEvent.click(screen.getByText("Make it cheaper"));
    expect(await screen.findByText(COST_REFUSED)).toBeTruthy();
  });
});

// ---------------------------------------------------------------------------
// (e) the Google free-tier line
// ---------------------------------------------------------------------------
describe("the Google free-tier line", () => {
  function renderApiKeys() {
    render(
      <ApiKeys connected={true} providers={[]} onConnect={vi.fn()} onRemove={vi.fn()} />,
    );
  }

  it("shows the frozen sentence byte-for-byte under Google", () => {
    renderApiKeys();
    expect(document.body.textContent).toContain(GOOGLE_LINE);
  });

  // The earlier version of this test asserted an <a href> and called it "a real,
  // openable link". The href proved only that an anchor existed in the DOM — the
  // webview has no way to open a URL at all (main.rs registers three commands, and
  // none of them is openExternal; Markdown.tsx states the rule). So the assertion
  // passed while the control was dead. What is pinned now is the honest shape: the
  // address is selectable text a person can copy, and there is NO anchor inviting a
  // click that would do nothing.
  it("shows the address as selectable text, never as a dead link", () => {
    renderApiKeys();
    expect(screen.queryByRole("link", { name: /aistudio/ })).toBeNull();
    expect(document.querySelector("a[href*='aistudio']")).toBeNull();
    const address = screen.getByText("aistudio.google.com/apikey");
    expect(address.tagName).toBe("SPAN");
    expect(address.className).toContain("select-all");
  });
});
