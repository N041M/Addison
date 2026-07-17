// Routine library — engineering-spec §6.5.
//
// TODO(step 8): the Routine engine (RoutineBuilder + RoutineEngine) lands in
// build step 8, only once steps 2–6 are solid. This pane is intentionally left
// as a stub for step 7 — no wiring, no `ipc.runRoutine`, no variable prompts —
// so the shell has a place for it without pulling step-8 behaviour forward.

export function RoutineLibrary() {
  return (
    <div className="rounded-card border border-line bg-surface p-4">
      <h3 className="text-base font-semibold text-ink">Saved routines</h3>
      <p className="mt-1 text-sm text-muted">
        Once you've done something a few times, Addison will be able to save it
        as a routine you can run again. This arrives in a later update.
      </p>
    </div>
  );
}
