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
      className="border-t border-line bg-surface px-6 py-4"
    >
      <h3 className="text-base font-semibold text-ink">
        Addison wants to add a widget: {proposal.title}
      </h3>
      {proposal.summary && (
        <p className="mt-1 text-sm text-muted">{proposal.summary}</p>
      )}
      <p className="mt-2 font-mono text-fact text-faint">
        {proposal.kind === "routine" ? "runs a saved routine" : "shows a value from Addison"}
      </p>

      <div className="mt-4 flex gap-3">
        <button
          type="button"
          onClick={onAdd}
          className="rounded-pill bg-fern px-5 py-2 text-sm font-semibold text-on-accent hover:bg-fern-deep"
        >
          Add widget
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-sm px-2 py-2 text-sm font-medium text-muted hover:text-ink-soft"
        >
          Not now
        </button>
      </div>
    </section>
  );
}
