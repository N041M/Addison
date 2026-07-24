// "Make it cheaper" confirmation card (Phase-2 step 4) — mirrors the routine /
// widget proposal cards' calm inline look and gating.
//
// Addison drafts the plan CORE-side (costPlan.propose) and holds it; nothing
// changes until the person presses the confirm button. EVERY field is canned in
// the core (the model authors none): the card just renders the fixed skill NAME
// and its FULL instructions text (contract F3) so the person sees exactly what
// guidance note is being added, followed by the frozen plain-language summary.
//
// On apply the core validates, saves a restore point FIRST, and refuses the whole
// change if that restore point can't be saved — so a refusal is a normal
// {ok:false} with a plain sentence, which the card shows inline (never a crash,
// never a stack trace).

import { useState } from "react";
import { ipc } from "../ipc/client";
import type { CostPlan } from "../types/ui";

// Frozen copy (contract D5) — byte-for-byte.
const SUMMARY =
  "Addison will add this guidance note and switch model picking to prefer cheaper " +
  "models. Your current setup is saved as a restore point first — one click in " +
  "Settings puts it back.";
const REFUSED =
  "Addison couldn't save the restore point that goes with this change, so nothing " +
  "was changed. Try again in a moment.";

interface Props {
  plan: CostPlan;
  onDismiss: () => void;
  /** Called after the plan is successfully applied, so callers can refresh. */
  onApplied?: () => void;
}

export function CostPlanCard({ plan, onDismiss, onApplied }: Props) {
  const [status, setStatus] = useState<"idle" | "working" | "error">("idle");
  const [error, setError] = useState("");
  const working = status === "working";

  async function apply() {
    setStatus("working");
    setError("");
    try {
      const res = await ipc.applyCostPlan(true);
      if (!res.ok) {
        setStatus("error");
        setError(res.error || REFUSED);
        return;
      }
      setStatus("idle");
      onApplied?.();
      onDismiss();
    } catch {
      setStatus("error");
      setError(REFUSED);
    }
  }

  function decline() {
    ipc.applyCostPlan(false).catch(() => {});
    onDismiss();
  }

  return (
    <section
      aria-label="Switch to cheaper models?"
      className="animate-[fade-rise_160ms_ease-out] border-t border-line bg-surface px-6 py-4"
    >
      <h3 className="text-base font-semibold text-ink">Switch to cheaper models?</h3>

      <p className="mt-2 text-sm font-semibold text-ink-soft">{plan.skillName}</p>
      <p className="mt-1 whitespace-pre-wrap border-l-2 border-line pl-4 text-sm text-ink-soft">
        {plan.skillInstructions}
      </p>

      <p className="mt-3 text-sm text-muted">{SUMMARY}</p>

      {status === "error" && <p className="mt-2 text-fine text-danger">{error}</p>}

      <div className="mt-4 flex gap-3">
        <button
          type="button"
          onClick={() => void apply()}
          disabled={working}
          className="rounded-pill bg-fern px-5 py-2 text-sm font-semibold text-on-accent hover:bg-fern-deep disabled:opacity-50"
        >
          {working ? "Saving…" : "Make it cheaper"}
        </button>
        <button
          type="button"
          onClick={decline}
          disabled={working}
          className="rounded-sm px-2 py-2 text-sm font-medium text-muted hover:text-ink-soft disabled:opacity-50"
        >
          Not now
        </button>
      </div>
    </section>
  );
}
