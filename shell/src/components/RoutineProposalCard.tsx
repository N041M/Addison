// Routine confirmation card — engineering-spec §6.3.
//
// Renders the core's plain-language preview of a drafted routine: what it will
// do each time, and exactly which values became fill-in-each-time variables
// (the generalization is a heuristic, so the user must see it before saving).
// Reuses the PermissionCard visual pattern — a calm inline card, one obvious
// choice, never a modal. Nothing is saved until "Save routine" is pressed.

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
      className="animate-[fade-rise_160ms_ease-out] border-t border-line bg-surface px-6 py-4"
    >
      <h3 className="text-base font-semibold text-ink">Save these steps as a routine?</h3>
      <p className="mt-1 text-sm text-muted">
        Addison can repeat this for you whenever you ask. Here's what it would do:
      </p>

      <ol className="mt-3 space-y-1 border-l-2 border-line pl-4">
        {proposal.steps.map((step) => (
          <li key={step} className="text-sm text-ink-soft">
            {step}
          </li>
        ))}
      </ol>

      {proposal.variables.length > 0 && (
        <p className="mt-3 text-sm text-muted">
          Each time it runs, Addison will ask:{" "}
          {proposal.variables.map((v) => `"${v.prompt}"`).join(" ")}
        </p>
      )}

      <label className="mt-4 block text-sm font-medium text-ink-soft">
        Name for this routine
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="mt-1 w-full max-w-md rounded border border-line bg-paper px-3 py-2 text-base text-ink"
        />
      </label>

      <div className="mt-4 flex gap-3">
        <button
          type="button"
          onClick={() => onSave(name.trim() || proposal.name)}
          className="rounded-sm bg-fern px-4 py-2 text-sm font-semibold text-on-accent hover:bg-fern-deep"
        >
          Save routine
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-sm border border-line bg-paper px-4 py-2 text-sm font-medium text-ink-soft hover:border-muted"
        >
          Not now
        </button>
      </div>
    </section>
  );
}
