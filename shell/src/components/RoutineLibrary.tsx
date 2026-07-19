// Routine library — engineering-spec §6.5.
//
// Lists saved routines with name, description, last-run time, "Run now"
// (prompting first for any variables without defaults), and delete. v1 has no
// step editing (§6.5/§10): structural changes are "delete and recreate via
// conversation", so the only affordances here are run, rename-free metadata
// display, and remove. Plain language throughout; no jargon.

import { useEffect, useState } from "react";
import { ipc, isEngineConnected } from "../ipc/client";
import { asRecord, normalizeVariables } from "../lib/parse";

// One step of a routine's declarative plan (spec §6.1). The core sends these on
// `routine.list` ONLY under the Developer profile; they are rendered READ-ONLY
// (§6.5) — there is deliberately no code field and no edit affordance.
interface PlanStep {
  stepId: string;
  toolId: string;
  argsTemplate: unknown;
  dependsOn: string[];
  onFailure: string;
}

interface RoutineRow {
  id: string;
  name: string;
  description: string;
  runCount: number;
  lastRunAt: number | null;
  variables: { name: string; prompt: string; default: string | null }[];
  /** Developer profile only: the declarative plan, for read-only viewing. */
  planSteps?: PlanStep[];
  /** The mode the routine was saved under ("safe" | "open"), when the core sends it. */
  createdInMode?: "safe" | "open";
}

interface RunOutcome {
  ok: boolean;
  detail: string;
}

interface Props {
  /**
   * Developer profile only: allow revealing a routine's declarative plan
   * (READ-ONLY). Off/absent for Simple, so its routine list is byte-identical.
   */
  exposeRoutinePlan?: boolean;
  /**
   * OPEN/Developer mode is active — tag dev-created routines (created_in_mode
   * "open") with the blocky "DEV" annotation. Simple never sees such routines
   * (core-filtered), so this stays false there.
   */
  developer?: boolean;
  /**
   * Changes whenever the active profile changes (its id). A mode switch hides or
   * reveals dev-created routines, so re-fetch the list when this changes.
   */
  refreshKey?: string;
}

export function RoutineLibrary({ exposeRoutinePlan = false, developer = false, refreshKey }: Props) {
  const connected = isEngineConnected();
  const [routines, setRoutines] = useState<RoutineRow[]>([]);
  const [loaded, setLoaded] = useState(false);
  // Per-routine transient UI state.
  const [filling, setFilling] = useState<string | null>(null); // routine collecting variables
  const [values, setValues] = useState<Record<string, string>>({});
  const [running, setRunning] = useState<string | null>(null);
  const [outcome, setOutcome] = useState<Record<string, RunOutcome>>({});
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null);
  const [planOpen, setPlanOpen] = useState<Record<string, boolean>>({}); // Developer: expanded plans

  useEffect(() => {
    if (!connected) {
      setLoaded(true);
      return;
    }
    refresh();
  }, [connected, refreshKey]);

  function refresh() {
    ipc
      .listRoutines()
      .then((res) => {
        setRoutines(normalizeRoutines(res));
        setLoaded(true);
      })
      .catch(() => setLoaded(true));
  }

  function startRun(routine: RoutineRow) {
    const needsInput = routine.variables.filter((v) => !v.default);
    if (needsInput.length > 0 && filling !== routine.id) {
      // Ask for the blanks first (§6.5) — prefill what has defaults.
      const prefill: Record<string, string> = {};
      for (const v of routine.variables) if (v.default) prefill[v.name] = v.default;
      setValues(prefill);
      setFilling(routine.id);
      return;
    }
    void executeRun(routine);
  }

  async function executeRun(routine: RoutineRow) {
    setFilling(null);
    setRunning(routine.id);
    setOutcome((prev) => ({ ...prev, [routine.id]: undefined as unknown as RunOutcome }));
    try {
      const res = (await ipc.runRoutine(routine.id, values)) as Record<string, unknown>;
      const ok = res?.ok === true;
      const detail =
        typeof res?.detail === "string" && res.detail
          ? res.detail
          : ok
            ? "Done — every step finished."
            : "It didn't finish. Nothing else was changed.";
      setOutcome((prev) => ({ ...prev, [routine.id]: { ok, detail } }));
      refresh(); // pick up run count / last-run time
    } catch (err) {
      const detail = err instanceof Error ? err.message : "That routine couldn't run.";
      setOutcome((prev) => ({ ...prev, [routine.id]: { ok: false, detail } }));
    } finally {
      setRunning(null);
      setValues({});
    }
  }

  function removeRoutine(id: string) {
    if (confirmingDelete !== id) {
      setConfirmingDelete(id);
      return;
    }
    setConfirmingDelete(null);
    ipc
      .deleteRoutine(id)
      .then(refresh)
      .catch(() => {
        /* leave the list as-is; the next refresh reconciles */
      });
  }

  if (!loaded) {
    return <p className="text-meta text-muted">Looking for your routines…</p>;
  }

  if (routines.length === 0) {
    return (
      <p className="text-meta text-muted">
        {connected
          ? "None yet. After Addison does something for you, look for " +
            "“Save these steps as a routine” — saved ones appear here."
          : "You can see and run your saved routines here once Addison's engine is connected."}
      </p>
    );
  }

  return (
    <ul className="flex flex-col gap-2">
      {routines.map((routine) => (
        <li
          key={routine.id}
          className="rounded border border-line bg-paper px-[14px] py-2.5"
        >
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              {developer && routine.createdInMode === "open" && (
                <span className="mb-0.5 inline-block border-l-2 border-fern pl-1.5 text-tag font-semibold uppercase tracking-caps-wide text-fern-deep">
                  Dev
                </span>
              )}
              <p className="text-action font-semibold text-ink">{routine.name}</p>
              <p className="mt-px text-fine text-faint">{runSummary(routine)}</p>
            </div>
            <div className="flex shrink-0 items-center gap-1.5">
              <button
                type="button"
                disabled={running === routine.id}
                onClick={() => startRun(routine)}
                className="rounded-pill bg-fern-tint px-[14px] py-1.5 text-xs font-semibold text-fern-deep hover:opacity-85 disabled:opacity-60"
              >
                {running === routine.id ? "Running…" : "Run"}
              </button>
              <button
                type="button"
                onClick={() => removeRoutine(routine.id)}
                className="px-1 py-1.5 text-xs font-medium text-faint hover:text-muted"
              >
                {confirmingDelete === routine.id ? "Really remove?" : "Remove"}
              </button>
            </div>
          </div>

            {filling === routine.id && (
              <div className="mt-3 rounded border border-line bg-paper p-3">
                {routine.variables
                  .filter((v) => !v.default)
                  .map((v) => (
                    <label key={v.name} className="mb-2 block text-sm font-medium text-ink-soft">
                      {v.prompt}
                      <input
                        type="text"
                        value={values[v.name] ?? ""}
                        onChange={(e) =>
                          setValues((prev) => ({ ...prev, [v.name]: e.target.value }))
                        }
                        className="mt-1 w-full rounded border border-line bg-surface px-3 py-2 text-base text-ink"
                      />
                    </label>
                  ))}
                <div className="mt-2 flex gap-2">
                  <button
                    type="button"
                    onClick={() => void executeRun(routine)}
                    className="rounded-sm bg-fern px-3 py-1.5 text-sm font-semibold text-on-accent hover:bg-fern-deep"
                  >
                    Start
                  </button>
                  <button
                    type="button"
                    onClick={() => setFilling(null)}
                    className="rounded-sm border border-line bg-surface px-3 py-1.5 text-sm font-medium text-ink-soft"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}

            {outcome[routine.id] && (
              <p
                className={
                  "mt-2 text-sm " +
                  (outcome[routine.id].ok ? "text-fern-deep" : "text-ink-soft")
                }
              >
                {outcome[routine.id].detail}
              </p>
            )}

            {exposeRoutinePlan && routine.planSteps && routine.planSteps.length > 0 && (
              <div className="mt-2">
                <button
                  type="button"
                  onClick={() =>
                    setPlanOpen((prev) => ({ ...prev, [routine.id]: !prev[routine.id] }))
                  }
                  aria-expanded={Boolean(planOpen[routine.id])}
                  className="text-xs font-medium text-muted hover:text-ink-soft"
                >
                  {planOpen[routine.id] ? "Hide plan" : "View plan"}
                </button>
                {planOpen[routine.id] && (
                  <PlanView steps={routine.planSteps} />
                )}
              </div>
            )}
          </li>
        ))}
      </ul>
  );
}

// Read-only rendering of a routine's declarative plan (§6.5). No inputs, no
// buttons, no reordering — viewing only. Compact and monospace so the shape of
// the plan is legible to a developer.
function PlanView({ steps }: { steps: PlanStep[] }) {
  return (
    <ol className="mt-2 space-y-2 rounded border border-line bg-paper p-3 font-mono text-xs text-ink-soft">
      {steps.map((step, i) => (
        <li
          key={step.stepId || i}
          className="border-t border-line pt-2 first:border-t-0 first:pt-0"
        >
          <div className="text-ink">
            <span className="text-muted">step</span> {step.stepId || `#${i + 1}`}{" "}
            <span className="text-muted">·</span> {step.toolId}
          </div>
          {step.dependsOn.length > 0 && (
            <div className="mt-0.5">
              <span className="text-muted">depends on</span> {step.dependsOn.join(", ")}
            </div>
          )}
          {step.onFailure && (
            <div className="mt-0.5">
              <span className="text-muted">on failure</span> {step.onFailure}
            </div>
          )}
          <pre className="mt-1 overflow-x-auto whitespace-pre-wrap">
            {formatArgs(step.argsTemplate)}
          </pre>
        </li>
      ))}
    </ol>
  );
}

function formatArgs(args: unknown): string {
  if (args === undefined || args === null) return "{}";
  try {
    return JSON.stringify(args, null, 2);
  } catch {
    return String(args);
  }
}

function runSummary(routine: RoutineRow): string {
  if (!routine.runCount) return "Never run yet";
  const times = routine.runCount === 1 ? "once" : `${routine.runCount} times`;
  if (!routine.lastRunAt) return `Run ${times}`;
  const when = new Date(routine.lastRunAt * 1000).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
  return `Run ${times} — last on ${when}`;
}

function normalizeRoutines(result: unknown): RoutineRow[] {
  const record = asRecord(result);
  const list = record && Array.isArray(record.routines) ? record.routines : [];
  const out: RoutineRow[] = [];
  for (const item of list) {
    const r = asRecord(item);
    if (!r || typeof r.id !== "string" || typeof r.name !== "string") continue;
    out.push({
      id: r.id,
      name: r.name,
      description: typeof r.description === "string" ? r.description : "",
      runCount: typeof r.runCount === "number" ? r.runCount : 0,
      lastRunAt: typeof r.lastRunAt === "number" ? r.lastRunAt : null,
      variables: normalizeVariables(r.variables),
      // Present only under the Developer profile; absent (undefined) otherwise.
      planSteps: Array.isArray(r.planSteps) ? normalizePlanSteps(r.planSteps) : undefined,
      // The mode the routine was saved under, when the core forwards it (camel or
      // snake). Drives the Developer "DEV" tag.
      createdInMode:
        r.createdInMode === "open" || r.createdInMode === "safe"
          ? (r.createdInMode as "open" | "safe")
          : r.created_in_mode === "open" || r.created_in_mode === "safe"
            ? (r.created_in_mode as "open" | "safe")
            : undefined,
    });
  }
  return out;
}

function normalizePlanSteps(raw: unknown[]): PlanStep[] {
  return raw.flatMap((s) => {
    if (!s || typeof s !== "object") return [];
    const rs = s as Record<string, unknown>;
    return [
      {
        stepId: typeof rs.stepId === "string" ? rs.stepId : "",
        toolId: typeof rs.toolId === "string" ? rs.toolId : "",
        // argsTemplate is free-form (rendered as pretty JSON), so pass it through.
        argsTemplate: rs.argsTemplate,
        dependsOn: Array.isArray(rs.dependsOn)
          ? rs.dependsOn.filter((d): d is string => typeof d === "string")
          : [],
        onFailure: typeof rs.onFailure === "string" ? rs.onFailure : "",
      },
    ];
  });
}
