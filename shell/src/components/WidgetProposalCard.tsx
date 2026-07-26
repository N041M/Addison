// Widget confirmation card — mirrors RoutineProposalCard's look and gating.
//
// Addison drafts a widget spec from the conversation (widget.proposeFromConversation)
// and holds it in the core; nothing is saved until the user presses "Add widget"
// (widget.confirmSave {accept:true}). A widget is a DECLARATIVE spec — a saved-
// routine Run pill or a whitelisted stat display — never code. Saving is display-
// only (LOW-risk), so there's no permission card here; a routine widget's routine
// keeps its own gates when it's actually run.

import type { WidgetProposal } from "../types/ui";

interface Props {
  proposal: WidgetProposal;
  onAdd: () => void;
  onCancel: () => void;
}

export function WidgetProposalCard({ proposal, onAdd, onCancel }: Props) {
  return (
    <section
      aria-label="Add this widget?"
      className="animate-[fadeRise_.2s_ease_both] rounded-[7px] border border-rail bg-panel px-3.5 py-3"
    >
      <h3 className="m-0 text-[12px] font-medium text-ink">
        Addison wants to add a widget: {proposal.title}
      </h3>
      {proposal.summary && (
        <p className="m-0 mt-1.5 text-[12px] leading-[1.55] text-muted">{proposal.summary}</p>
      )}
      <p className="m-0 mt-2 font-mono text-[10px] text-disabled">
        {proposal.kind === "routine" ? "runs a saved routine" : "shows a value from Addison"}
      </p>

      <div className="mt-3 flex items-baseline gap-5">
        <button
          type="button"
          onClick={onAdd}
          className="text-[12px] text-accent transition-colors hover:text-ink max-md:min-h-[44px]"
        >
          Add widget
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
