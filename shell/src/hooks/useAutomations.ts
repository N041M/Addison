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
// Nothing here is ever the job FILE, and nothing here can arm: arming is a TOOL
// behind the ordinary card plus a typed code, and there is deliberately no
// `automation.arm` on the Frontend→Core surface at all.

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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshAutomations = useCallback(() => {
    if (!connected) return;
    ipc
      .listAutomations()
      .then((rows) => {
        setAutomations(rows);
        setLoaded(true);
      })
      .catch(() => {
        // Keep the last-known list rather than blanking the section; still stop the
        // looking-for line.
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

  return {
    automations,
    automationsLoaded: loaded,
    status,
    statusFailed,
    busy,
    error,
    refreshAutomations,
    refreshArmedState,
    handleRemove,
  };
}

export type AutomationsCardState = ReturnType<typeof useAutomations>;
