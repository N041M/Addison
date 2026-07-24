// The two step-4 conversational offers: "add my own model server" and "make it
// cheaper". Deliberately the SAME shape as useWidgets' proposal flow, because it
// is the same mechanism and the repo already decided that mechanism (the widget /
// routine precedent): after a turn, the core is asked whether the person's own
// words called for one of these; if so the core drafts the plan and holds it,
// nothing actionable rides on the model's reply, a card appears, and nothing is
// applied until the person presses the button on it.
//
// Why the trigger is a keyword test over the USER's text and never the model's
// answer: the reply must never be able to arm a card. The core enforces the same
// rule on its side (rpc/providers._current_turn_user_texts reads role=="user"
// only), so this is the frontend half of one decision, not a second policy.
//
// One card at a time. If a turn matches both, the endpoint card wins — it is the
// one with a key field, and stacking two consent cards under the composer is how
// a person ends up confirming the one they did not read.

import { useState } from "react";
import { ipc } from "../ipc/client";
import type { CostPlan, EndpointProposal } from "../types/ui";

// Deliberately narrow. A false negative costs one prose answer telling the person
// where the control is (primary.txt already says that); a false positive puts an
// unasked-for consent card under the composer, which is worse.
const WANTS_ENDPOINT =
  /\b(add|connect|hook up|set up|setup)\b[^.?!]*\b(server|endpoint|ollama|lm studio)\b/i;
const WANTS_CHEAPER =
  /\b(cheaper|cheapest|cost|costs|costly|expensive|save money|free model|less money|spend less)\b/i;

// Plain, and each names where the change lives so the person can find — and undo
// — it. Both live in Settings, alongside the restore points.
export const ENDPOINT_ADDED_BANNER = "Added your server — it's in Settings under your services.";
export const COST_PLAN_APPLIED_BANNER =
  "Addison will keep answers short and prefer cheaper models. Your previous setup is " +
  "saved as a restore point in Settings.";

export interface OffersState {
  endpointProposal: EndpointProposal | null;
  costPlan: CostPlan | null;
  /** Called with the user's own text after a turn finishes. */
  maybeProposeOffers: (userText: string) => void;
  dismissEndpointProposal: () => void;
  dismissCostPlan: () => void;
  handleEndpointAdded: () => void;
  handleCostPlanApplied: () => void;
}

export function useOffers(
  isEngineConnected: () => boolean,
  setStatusBanner: (text: string | null) => void,
  refreshAfterApply: () => void,
): OffersState {
  const [endpointProposal, setEndpointProposal] = useState<EndpointProposal | null>(null);
  const [costPlan, setCostPlan] = useState<CostPlan | null>(null);

  function maybeProposeOffers(userText: string) {
    if (!isEngineConnected()) return;
    if (WANTS_ENDPOINT.test(userText)) {
      // The core decides whether the turn really named an address; a `null` here
      // means "nothing to add" and stays silent — Addison has already answered in
      // prose telling the person what to paste.
      ipc
        .proposeEndpoint()
        .then((proposal) => {
          if (proposal) {
            setCostPlan(null);
            setEndpointProposal(proposal);
          }
        })
        .catch(() => {
          /* nothing to propose — stay quiet, exactly as the widget flow does */
        });
      return;
    }
    if (WANTS_CHEAPER.test(userText)) {
      ipc
        .proposeCostPlan()
        .then((plan) => plan && setCostPlan(plan))
        .catch(() => {
          /* stay quiet */
        });
    }
  }

  function dismissEndpointProposal() {
    setEndpointProposal(null);
    // Let the core drop its held draft too (accept:false) — the widget precedent.
    ipc.confirmAddEndpoint("", false).catch(() => {});
  }

  function dismissCostPlan() {
    setCostPlan(null);
    ipc.applyCostPlan(false).catch(() => {});
  }

  function handleEndpointAdded() {
    setStatusBanner(ENDPOINT_ADDED_BANNER);
    refreshAfterApply();
  }

  function handleCostPlanApplied() {
    setStatusBanner(COST_PLAN_APPLIED_BANNER);
    refreshAfterApply();
  }

  return {
    endpointProposal,
    costPlan,
    maybeProposeOffers,
    dismissEndpointProposal,
    dismissCostPlan,
    handleEndpointAdded,
    handleCostPlanApplied,
  };
}

export { WANTS_CHEAPER, WANTS_ENDPOINT };
