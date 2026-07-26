// Routine confirmation card — engineering-spec §6.3.
//
// Renders the core's plain-language preview of a drafted routine: what it will
// do each time, and exactly which values became fill-in-each-time variables
// (the generalization is a heuristic, so the user must see it before saving).
// Reuses the PermissionCard visual pattern — a calm inline block, one obvious
// choice, never a modal. Nothing is saved until "Save routine" is pressed. The
// DARK direction restyles it (flat `panel`, 1px `rail` border, 7px radius, 12px
// text, accent confirm / muted dismiss); every sentence is unchanged.

import { useState } from "react";

export interface RoutineProposal {
  routineId: string;
  name: string;
  description: string;
  steps: string[];
  variables: { name: string; prompt: string; default: string | null }[];
}

interface Props {
  proposal: RoutineProposal;
  onSave: (name: string) => void;
  onCancel: () => void;
}

export function RoutineProposalCard({ proposal, onSave, onCancel }: Props) {
  const [name, setName] = useState(proposal.name);

  return (
    <section
      aria-label="Save these steps as a routine?"
      className="animate-[fadeRise_.2s_ease_both] rounded-[7px] border border-rail bg-panel px-3.5 py-3"
    >
      <h3 className="m-0 text-[12px] font-medium text-ink">Save these steps as a routine?</h3>
      <p className="m-0 mt-1.5 text-[12px] leading-[1.55] text-muted">
        Addison can repeat this for you whenever you ask. Here's what it would do:
      </p>

      <ol className="m-0 mt-2.5 list-none border-l-2 border-rail p-0 pl-3.5">
        {proposal.steps.map((step) => (
          <li key={step} className="text-[12px] leading-[1.6] text-ink-soft">
            {step}
          </li>
        ))}
      </ol>

      {proposal.variables.length > 0 && (
        <p className="m-0 mt-2.5 text-[12px] leading-[1.55] text-muted">
          Each time it runs, Addison will ask:{" "}
          {proposal.variables.map((v) => `"${v.prompt}"`).join(" ")}
        </p>
      )}

      <label className="mt-3 block text-[12px] text-muted">
        Name for this routine
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="mt-1.5 w-full rounded-[5px] border border-line bg-paper px-2.5 py-2 text-[12px] text-ink outline-none focus:border-track-hi"
        />
      </label>

      <div className="mt-3 flex items-baseline gap-5">
        <button
          type="button"
          onClick={() => onSave(name.trim() || proposal.name)}
          className="text-[12px] text-accent transition-colors hover:text-ink max-md:min-h-[44px]"
        >
          Save routine
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="text-[12px] text-muted transition-colors hover:text-ink max-md:min-h-[44px]"
        >
          Not now
        </button>
      </div>
    </section>
  );
}
