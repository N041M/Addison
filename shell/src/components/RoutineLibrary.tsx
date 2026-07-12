// Routine library — engineering-spec §6.5.
// Lists saved Routines: name, description, last-run time, "Run now" (prompts for
// variables without defaults, then calls the engine), and edit/delete.
// v1 editing = name/description/variable-defaults only; structural changes are
// "delete and recreate via conversation" (§6.5, §10).

import { ipc } from "../ipc/client";

interface RoutineSummary {
  id: string;
  name: string;
  description: string;
  lastRunAt?: number;
}

interface Props {
  routines: RoutineSummary[];
}

export function RoutineLibrary({ routines }: Props) {
  return (
    <div className="routine-library">
      {routines.map((r) => (
        <div key={r.id} className="routine-card">
          <h4>{r.name}</h4>
          <p>{r.description}</p>
          <button onClick={() => ipc.runRoutine(r.id, {})}>Run now</button>
          {/* TODO(step 8): prompt for variables without defaults before running */}
        </div>
      ))}
    </div>
  );
}
