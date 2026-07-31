// Routine library — engineering-spec §6.5, in the dark direction's row idiom.
//
// Lists saved routines with name, a mono run summary, "Run" (prompting first for
// any variables without defaults), and remove. v1 has no step editing (§6.5/§10):
// structural changes are "delete and recreate via conversation", so the only
// affordances here are run, metadata display, and remove. Plain language
// throughout; no jargon.
//
// Remove is the one control on this surface that keeps the `danger` token — it
// really does destroy something the person made, and the two-press confirm
// ("Really remove?") is unchanged.

import { useEffect, useState } from "react";
import { ipc, isEngineConnected } from "../ipc/client";
import { asRecord, normalizeVariables } from "../lib/parse";
import { RowAction, SurfaceRow } from "./Surface";

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
  // WHICH routine the values above were entered for. Without it the map is shared
  // across the whole library: fill routine A's `path`, then run routine B (which
  // needs no input, so it skips the fill step entirely and runs immediately) and
  // B's own default `path` is overridden by A's value. The engine is safe against
  // UNKNOWN names — it builds defaults from routine.variables and resolve_template
  // only reads names the template mentions — so a stray key is inert; a name
  // COLLISION is not, and `path`/`branch`/`message` are exactly the names two
  // routines are most likely to share.
  const [valuesFor, setValuesFor] = useState<string | null>(null);
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
      setValuesFor(routine.id);
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
      // Only ever send values that were entered FOR this routine. Anything else is
      // another routine's answers wearing the same variable name.
      const payload = valuesFor === routine.id ? values : {};
      const res = (await ipc.runRoutine(routine.id, payload)) as Record<string, unknown>;
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
      // Both halves, together. `values` and `valuesFor` are one fact spelled in
      // two variables — which answers WHICH routine the answers belong to — and
      // clearing only the answers leaves an id pointing at an empty map. Harmless
      // today (the payload is `{}` either way), and exactly the shape of the bug
      // this pair was introduced to fix.
      setValues({});
      setValuesFor(null);
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
    return <SurfaceRow wrap name="Looking for your routines…" />;
  }

  if (routines.length === 0) {
    // "None yet" is a claim about the person's own saved routines, and while the
    // engine is down this surface cannot see them — it never asked. Saying it
    // anyway would be the surface asserting a fact it doesn't have (the sibling
    // sections all keep this distinction), so the disconnected case gets its own
    // sentence and no count-like value beside it.
    return connected ? (
      <SurfaceRow name="None yet" value="saved steps appear here" />
    ) : (
      <SurfaceRow wrap name="You can see and run your saved routines here once Addison's engine is connected." />
    );
  }

  return (
    <>
      {routines.map((routine) => (
        <SurfaceRow
          key={routine.id}
          tag={
            developer && routine.createdInMode === "open" ? (
              <span className="mb-1 inline-block border-l-2 border-accent pl-1.5 text-[9.5px] font-medium uppercase tracking-[.09em] text-accent">
                Dev
              </span>
            ) : undefined
          }
          name={routine.name}
          value={runSummary(routine)}
          actions={
            <>
              <RowAction
                disabled={running === routine.id}
                ariaLabel={`Run ${routine.name}`}
                onClick={() => startRun(routine)}
              >
                {running === routine.id ? "Running…" : "Run"}
              </RowAction>
              <RowAction
                tone="danger"
                ariaLabel={`Remove ${routine.name}`}
                onClick={() => removeRoutine(routine.id)}
              >
                {confirmingDelete === routine.id ? "Really remove?" : "Remove"}
              </RowAction>
            </>
          }
        >
          {filling === routine.id && (
            <div className="mt-2.5 border-l-2 border-rail pl-3.5">
              {routine.variables
                .filter((v) => !v.default)
                .map((v) => (
                  <label key={v.name} className="mb-2 block text-[12px] text-ink-soft">
                    {v.prompt}
                    <input
                      type="text"
                      value={values[v.name] ?? ""}
                      onChange={(e) =>
                        setValues((prev) => ({ ...prev, [v.name]: e.target.value }))
                      }
                      className="mt-1 block w-full border-b border-line bg-transparent py-1 font-mono text-[11px] text-ink outline-none focus:border-track-hi"
                    />
                  </label>
                ))}
              <div className="mt-2 flex items-baseline gap-5">
                <RowAction onClick={() => void executeRun(routine)}>Start</RowAction>
                <RowAction tone="muted" onClick={() => setFilling(null)}>
                  Cancel
                </RowAction>
              </div>
            </div>
          )}

          {outcome[routine.id] && (
            <p className="m-0 mt-2 text-[12px] leading-[1.55] text-ink-soft">
              {outcome[routine.id].detail}
            </p>
          )}

          {exposeRoutinePlan && routine.planSteps && routine.planSteps.length > 0 && (
            <div className="mt-2">
              <RowAction
                mono
                onClick={() =>
                  setPlanOpen((prev) => ({ ...prev, [routine.id]: !prev[routine.id] }))
                }
                ariaLabel={planOpen[routine.id] ? "Hide plan" : "View plan"}
              >
                {planOpen[routine.id] ? "hide plan" : "view plan"}
              </RowAction>
              {planOpen[routine.id] && <PlanView steps={routine.planSteps} />}
            </div>
          )}
        </SurfaceRow>
      ))}
    </>
  );
}

// Read-only rendering of a routine's declarative plan (§6.5). No inputs, no
// buttons, no reordering — viewing only. Compact and monospace so the shape of
// the plan is legible to a developer.
function PlanView({ steps }: { steps: PlanStep[] }) {
  return (
    <ol className="mt-2 border-l-2 border-rail pl-3.5 font-mono text-[10.5px] leading-[1.6] text-ink-soft">
      {steps.map((step, i) => (
        <li key={step.stepId || i} className="border-t border-line pt-2 first:border-t-0 first:pt-0">
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
          <pre className="m-0 mt-1 overflow-x-auto whitespace-pre-wrap">
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
  if (!routine.runCount) return "never run yet";
  const times = routine.runCount === 1 ? "once" : `${routine.runCount} times`;
  if (!routine.lastRunAt) return `run ${times}`;
  const when = new Date(routine.lastRunAt * 1000).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  });
  return `run ${times} · last ${when}`;
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
