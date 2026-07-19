// Widgets (declarative specs), core-computed rail stats, the light routine
// mirror the rail needs, and the widget proposal flow. Extracted from App.tsx
// as a mechanical move: the state, its refreshers, the stats interval, and the
// handlers are unchanged.

import { useEffect, useState } from "react";
import type { Stats, Widget, WidgetProposal } from "../types/ui";
import { ipc, isEngineConnected } from "../ipc/client";
import type { RailRoutine, RunOutcome } from "../components/WidgetRail";
import { asRecord, normalizeVariables } from "../lib/parse";

interface UseWidgetsArgs {
  connected: boolean;
  railOpen: boolean;
  setStatusBanner: (text: string | null) => void;
}

export function useWidgets({ connected, railOpen, setStatusBanner }: UseWidgetsArgs) {
  // Widgets (declarative specs) + core-computed stats for the rail. Widgets are
  // proposed like routines (draft-in-core, saved only on confirm); the token
  // meter + connections cards read `stats`, refreshed on a light schedule.
  const [widgets, setWidgets] = useState<Widget[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [railRoutines, setRailRoutines] = useState<RailRoutine[]>([]);
  const [widgetProposal, setWidgetProposal] = useState<WidgetProposal | null>(null);

  // Refresh stats on a 60s interval WHILE the rail is open (cleared on hide /
  // unmount). No websockets, no busywork — the rail also refreshes on mount and
  // after each completed turn.
  useEffect(() => {
    if (!connected || !railOpen) return;
    const t = setInterval(() => refreshStats(), 60_000);
    return () => clearInterval(t);
  }, [connected, railOpen]);

  function refreshWidgets() {
    if (!isEngineConnected()) return;
    ipc
      .listWidgets()
      .then(setWidgets)
      .catch(() => {
        /* leave the rail on its last-known widgets if we can't read them */
      });
    // A routine widget needs its routine's variables to prompt on Run — keep a
    // light copy of the library alongside the widgets.
    ipc
      .listRoutines()
      .then((res) => setRailRoutines(normalizeRailRoutines(res)))
      .catch(() => {
        /* leave the routine metadata as-is */
      });
  }

  function refreshStats() {
    if (!isEngineConnected()) return;
    ipc
      .getStats()
      .then(setStats)
      .catch(() => {
        /* leave the token meter / connections on their last-known values */
      });
  }

  // --- Widgets (declarative specs): propose -> card -> explicit save ---------
  // Called after a turn whose user message mentioned a widget: draft one from the
  // conversation. A refusal (the core can't make one yet) is silent — no card.
  function maybeProposeWidget(userText: string) {
    if (!isEngineConnected() || !/widget/i.test(userText)) return;
    ipc
      .proposeWidget()
      .then((proposal) => setWidgetProposal(proposal))
      .catch(() => {
        /* refusal / nothing to propose — stay quiet */
      });
  }

  function handleAddWidget() {
    setWidgetProposal(null);
    ipc
      .confirmWidget(true)
      .then((res) => {
        if (res.ok) {
          setStatusBanner("Added the widget — it's in your rail.");
          refreshWidgets();
        } else if (res.error) {
          setStatusBanner(res.error);
        }
      })
      .catch((err) => {
        setStatusBanner(err instanceof Error ? err.message : "I couldn't add that widget.");
      });
  }

  function handleDismissWidgetProposal() {
    setWidgetProposal(null);
    // Let the core drop its held draft too (accept:false).
    ipc.confirmWidget(false).catch(() => {});
  }

  function handleSetWidgetPinned(id: string, pinned: boolean) {
    ipc
      .setWidgetPinned(id, pinned)
      .then((res) => {
        if (!res.ok && res.error) setStatusBanner(res.error);
        refreshWidgets();
      })
      .catch(() => setStatusBanner("Couldn't change that widget just now."));
  }

  function handleDeleteWidget(id: string) {
    ipc
      .deleteWidget(id)
      .then(() => refreshWidgets())
      .catch(() => setStatusBanner("Couldn't remove that widget just now."));
  }

  // Run a routine straight from its widget (§6.5 variable prompts happen in the
  // card). Returns the plain outcome for the card to show; refreshes stats since
  // a run may have used the model.
  async function handleRunWidgetRoutine(
    routineId: string,
    variables: Record<string, string>,
  ): Promise<RunOutcome> {
    try {
      const res = (await ipc.runRoutine(routineId, variables)) as Record<string, unknown>;
      const ok = res?.ok === true;
      const detail =
        typeof res?.detail === "string" && res.detail
          ? res.detail
          : ok
            ? "Done — every step finished."
            : "It didn't finish. Nothing else was changed.";
      refreshStats();
      return { ok, detail };
    } catch (err) {
      return { ok: false, detail: err instanceof Error ? err.message : "That routine couldn't run." };
    }
  }

  return {
    widgets,
    stats,
    railRoutines,
    widgetProposal,
    refreshWidgets,
    refreshStats,
    maybeProposeWidget,
    handleAddWidget,
    handleDismissWidgetProposal,
    handleSetWidgetPinned,
    handleDeleteWidget,
    handleRunWidgetRoutine,
  };
}

export type WidgetsState = ReturnType<typeof useWidgets>;

// Light copy of the routine library for the rail: just enough to prompt for a
// routine widget's variables on Run. Mirrors RoutineLibrary's normalizer.
function normalizeRailRoutines(result: unknown): RailRoutine[] {
  const record = asRecord(result);
  const list = record && Array.isArray(record.routines) ? record.routines : [];
  const out: RailRoutine[] = [];
  for (const item of list) {
    const r = asRecord(item);
    if (!r || typeof r.id !== "string" || typeof r.name !== "string") continue;
    // created_in_mode ("safe" | "open"), camel or snake — drives the Developer
    // "DEV" tag on dev-created routine widgets.
    const rawMode = r.createdInMode ?? r.created_in_mode;
    out.push({
      id: r.id,
      name: r.name,
      createdInMode: rawMode === "open" || rawMode === "safe" ? rawMode : undefined,
      variables: normalizeVariables(r.variables),
    });
  }
  return out;
}
