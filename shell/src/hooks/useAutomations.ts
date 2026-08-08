// The automations list (automation.list / automation.remove / automation.status;
// Phase-2 step 8, phase 4 of four). This hook owns the saved rows, what the
// OPERATING SYSTEM last said about them, the loading/busy flags and the removal
// handler. It mirrors useMcpServers, and App owns it for one reason the mcp hook
// spells out at its own restore entry:
//
// `automations` IS A SNAPSHOT-CAPTURED TABLE. A G3 restore can add or remove rows
// underneath an open Settings page, and every other captured table is re-read by
// App's `onRestored` closure. Phase 2 left this section self-fetching, so it was
// the one captured table nothing re-read: after a restore, Settings went on
// offering Remove for a row the core had already forgotten, and a row the restore
// brought back was not on screen at all. The fetch lives here so that closure can
// reach it.
//
// TWO REFRESHES, DELIBERATELY NOT ONE:
//
//   * `refreshAutomations` reads the saved ROWS. Local, cheap, and safe to do on
//     mount and after any change to configuration. The SECTION calls it again on
//     every visit: `create_automation` writes rows from CHAT, and the Settings
//     page unmounts between visits, so a list read only at launch would be
//     missing the automation somebody just asked Addison for (post-merge review
//     of phase 4).
//   * `refreshArmedState` asks the OPERATING SYSTEM what it is actually running,
//     and is called by the SECTION when it loads — never here on mount. Plan §5.6
//     is explicit that nothing polls and NOTHING CHECKS AT STARTUP (the mcp
//     temperament: no action the person did not just cause), and a hook that App
//     mounts at launch would turn "asked when the surface loads" into "asked every
//     time Addison opens". Owning the ANSWER here and leaving the ASK to the
//     surface is what keeps both halves true.
//
// A RESTORE RE-READS THE ROWS AND DOES NOT RE-ASK THE OS, and that is not an
// oversight. A restore puts configuration back; it can never arm and never disarms
// (plan §5.6 — there is no armed column to restore, and a one-action restore cannot
// perform the keyword ceremony), so what launchd holds is the same set of labels it
// held a moment ago. The cached answer stays true, and a row's armed-ness is
// recomputed from its own label on every render.
//
// THE ORPHAN, AND WHEN IT IS SEEN (2026-08-08, closing the KNOWN-GAPS entry).
// A restore CAN take a row away from a job the OS is still running: the config
// capture is REPLACE-ALL, so restoring a point from before an automation was written
// deletes its row while `<label>.plist` stays installed. The armed set and the rows
// then disagree, and the leftover label is an ORPHAN — a job with nothing on any
// surface to name it or stop it. The section reconciles the two answers it already
// has and renders one; `handleDisarmOrphan` below is the only thing that acts.
//
// THE LATENCY IS THE ACCEPTED COST. Because a restore re-reads the rows and does NOT
// re-ask the OS, an orphan created by a restore while Settings is open appears on the
// NEXT section load, not at once. Re-asking the OS in App's `onRestored` would close
// that window and is exactly the check-nobody-caused that §5.6 forbids — and the ask
// would be wrong on its own terms, since a restore cannot have changed what launchd
// holds. Nothing polls, here or anywhere. Reconciling on the next visit is the whole
// of the fix, and it is enough: the job keeps running either way, and the surface
// that can stop it is the one somebody has to open to see it.
//
// Nothing here is ever the job FILE, and nothing here can arm: arming is a TOOL
// behind the ordinary card plus a typed code, and there is deliberately no
// `automation.arm` on the Frontend→Core surface at all. `disarmOrphanAutomation` is
// the one exception to "this hook only reads and removes rows", and it is a
// TIGHTENING in the direction the exception is safe in — it can only stop something.

import { useCallback, useEffect, useState } from "react";
import type { Automation, AutomationStatus } from "../types/protocol";
import { ipc } from "../ipc/client";

interface UseAutomationsArgs {
  connected: boolean;
}

/** When a removal doesn't land and the core said nothing usable about why. The
 * core's own sentence is preferred whenever there is one — it knows which of the
 * refusals happened (the row is already gone, a restore point could not be saved,
 * or the job could not be switched off first). */
const REMOVE_FAILED = "Addison couldn't remove that automation just now.";

/** When switching off an orphaned job doesn't land and the core said nothing usable.
 * The core's own sentence is preferred whenever there is one — it knows which refusal
 * happened (the label isn't one Addison set up, the automation is saved again, or the
 * computer's scheduler wouldn't answer), and one of those is a fact this side has no
 * way to work out. */
const DISARM_ORPHAN_FAILED = "Addison couldn't switch that off just now.";

export function useAutomations({ connected }: UseAutomationsArgs) {
  const [automations, setAutomations] = useState<Automation[]>([]);
  // What the OPERATING SYSTEM said the last time the surface asked. `null` is "no
  // answer" — never "nothing armed", which is the guess that would let a running
  // job render as a quiet draft.
  const [status, setStatus] = useState<AutomationStatus | null>(null);
  const [statusFailed, setStatusFailed] = useState(false);
  // "not asked yet" vs "asked" — a slow first fetch must not render as a claim
  // that the person has no saved automations.
  const [loaded, setLoaded] = useState(false);
  // ...and "asked but not ANSWERED", which is a third thing again. The list is kept
  // on a failure rather than blanked, so `automations` alone cannot say whether it is
  // the core's current answer or the last one that arrived — and the ORPHAN
  // reconciliation is the one reader for which that difference is everything. An
  // empty list read as "nothing is saved" beside an armed label would render every
  // one of somebody's real automations as "running, but not saved here", on the
  // first load after a list fetch that failed. Same shape as `statusFailed`, for
  // the same reason: a guess in either direction is the failure.
  const [automationsFailed, setAutomationsFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshAutomations = useCallback(() => {
    if (!connected) return;
    ipc
      .listAutomations()
      .then((rows) => {
        setAutomations(rows);
        setAutomationsFailed(false);
        setLoaded(true);
      })
      .catch(() => {
        // Keep the last-known list rather than blanking the section; still stop the
        // looking-for line. But SAY that it is last-known: see the flag's comment.
        setAutomationsFailed(true);
        setLoaded(true);
      });
  }, [connected]);

  /** Ask the OS what it is running, right now. Called by the section when it loads
   * and at no other time (see the header): never stored, never polled. */
  const refreshArmedState = useCallback(() => {
    if (!connected) return;
    ipc
      .getAutomationStatus()
      .then((next) => {
        setStatus(next);
        setStatusFailed(false);
      })
      .catch(() => {
        // Not "nothing is armed" — nothing was ANSWERED. The section says so and
        // the rows stay silent about armed-ness.
        setStatus(null);
        setStatusFailed(true);
      });
  }, [connected]);

  useEffect(() => {
    // The rows only. The operating system is asked by the surface, not by an app
    // that has just started (plan §5.6).
    refreshAutomations();
  }, [refreshAutomations]);

  /** Forget an automation. The core disarms it first when the OS is holding it and
   * REFUSES the whole removal if it cannot — so a refusal is a resolved
   * {ok:false} carrying the core's own plain sentence, and the list is re-read
   * either way, because what is on screen after a press is a guess until the core
   * has been asked again. */
  const handleRemove = useCallback(
    async (id: string): Promise<void> => {
      setBusy(true);
      setError(null);
      try {
        const result = await ipc.removeAutomation(id);
        if (!result.ok) setError(result.error ?? REMOVE_FAILED);
      } catch {
        setError(REMOVE_FAILED);
      } finally {
        setBusy(false);
        refreshAutomations();
      }
    },
    [refreshAutomations],
  );

  /** Switch off a job the operating system is holding that no saved row can reach.
   * The LABEL is all there is to send — an orphan has no id, no name and no command,
   * which is the whole problem it exists to solve.
   *
   * BOTH ANSWERS ARE RE-READ, and both halves are re-read, because whether a label is
   * an orphan is a fact about the ROWS and the OS TOGETHER: a success takes the label
   * out of the armed set, and the one refusal a person can actually meet ("that
   * automation is saved again") means the rows on screen are stale. Asking the OS here
   * is not a check nobody caused — it is the direct result of a button somebody just
   * pressed, which is the same rule that puts the ask in the section's load. */
  const handleDisarmOrphan = useCallback(
    async (label: string): Promise<void> => {
      setBusy(true);
      setError(null);
      try {
        const result = await ipc.disarmOrphanAutomation(label);
        if (!result.ok) setError(result.error ?? DISARM_ORPHAN_FAILED);
      } catch {
        setError(DISARM_ORPHAN_FAILED);
      } finally {
        setBusy(false);
        refreshAutomations();
        refreshArmedState();
      }
    },
    [refreshAutomations, refreshArmedState],
  );

  return {
    automations,
    automationsLoaded: loaded,
    automationsFailed,
    status,
    statusFailed,
    busy,
    error,
    refreshAutomations,
    refreshArmedState,
    handleRemove,
    handleDisarmOrphan,
  };
}

export type AutomationsCardState = ReturnType<typeof useAutomations>;
