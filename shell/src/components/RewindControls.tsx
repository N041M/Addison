// Undo / rewind affordances — the safety net (design-doc §7.9, §7.9.1).
//
// Two distinct mechanisms, both plain-language buttons (never typed commands):
//   - "Undo last action" (this component) reverses the most recent file/state
//     change → `undo.undoLastAction`, and shows the plain detail the core hands
//     back ("Put back invoice_march.pdf"). It lives in the Activity strip —
//     panic buttons belong where the panic is.
//   - "Rewind to here" (a conversational reset → `undo.rewindConversation`)
//     lives as a hover affordance on each past user message, inside ChatThread,
//     wired to the same handler surface.
//
// Stop (halt an in-progress turn) and Retry (regenerate the last answer) are
// rendered in the composer / on the last message respectively — see ChatThread.

interface Props {
  hasUndoableActions: boolean;
  onUndoLastAction: () => void;
  lastUndoDetail?: string | null;
}

export function RewindControls({
  hasUndoableActions,
  onUndoLastAction,
  lastUndoDetail,
}: Props) {
  if (!hasUndoableActions && !lastUndoDetail) return null;

  return (
    <div className="flex flex-col gap-1.5">
      {hasUndoableActions && (
        <button
          type="button"
          onClick={onUndoLastAction}
          className="inline-flex w-fit items-center gap-2 rounded-lg border border-line bg-surface px-3 py-1.5 text-sm font-medium text-ink-soft hover:border-muted"
        >
          <span aria-hidden="true">↺</span>
          Undo last action
        </button>
      )}
      {lastUndoDetail && (
        <p className="text-sm text-muted">{lastUndoDetail}</p>
      )}
    </div>
  );
}
