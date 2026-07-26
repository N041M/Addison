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
      className="animate-[fadeRise_.2s_ease_both] rounded-[7px] border border-rail bg-panel px-3.5 py-3"
    >
      <h3 className="m-0 text-[12px] font-medium text-ink">Switch to cheaper models?</h3>

      <p className="m-0 mt-2 text-[12px] font-medium text-ink-soft">{plan.skillName}</p>
      <p className="m-0 mt-1.5 whitespace-pre-wrap border-l-2 border-rail pl-3.5 text-[12px] leading-[1.6] text-ink-soft">
        {plan.skillInstructions}
      </p>

      <p className="m-0 mt-2.5 text-[12px] leading-[1.55] text-muted">{SUMMARY}</p>

      {status === "error" && <p className="m-0 mt-2 text-[12px] text-danger">{error}</p>}

      <div className="mt-3 flex items-baseline gap-5">
        <button
          type="button"
          onClick={() => void apply()}
          disabled={working}
          className="text-[12px] text-accent transition-colors hover:text-ink disabled:cursor-not-allowed disabled:text-disabled max-md:min-h-[44px]"
        >
          {working ? "Saving…" : "Make it cheaper"}
        </button>
        <button
          type="button"
          onClick={decline}
          disabled={working}
          className="text-[12px] text-muted transition-colors hover:text-ink disabled:cursor-not-allowed disabled:text-disabled max-md:min-h-[44px]"
        >
          Not now
        </button>
      </div>
    </section>
  );
}
