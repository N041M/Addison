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
  /** True when the core reports something undone this session can be re-applied. */
  canRedo?: boolean;
  onRedoLastAction?: () => void;
}

export function RewindControls({
  hasUndoableActions,
  onUndoLastAction,
  lastUndoDetail,
  canRedo,
  onRedoLastAction,
}: Props) {
  if (!hasUndoableActions && !lastUndoDetail && !canRedo) return null;

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap gap-4">
        {hasUndoableActions && (
          <button
            type="button"
            onClick={onUndoLastAction}
            className="inline-flex w-fit items-center gap-1.5 text-[13px] font-medium text-muted hover:text-ink-soft"
          >
            <span aria-hidden="true">↺</span>
            Undo last action
          </button>
        )}
        {canRedo && onRedoLastAction && (
          <button
            type="button"
            onClick={onRedoLastAction}
            className="inline-flex w-fit items-center gap-1.5 text-[13px] font-medium text-muted hover:text-ink-soft"
          >
            <span aria-hidden="true">↻</span>
            Do it again
          </button>
        )}
      </div>
      {lastUndoDetail && <p className="text-[13px] text-muted">{lastUndoDetail}</p>}
    </div>
  );
}
