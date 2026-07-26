// The Snapshots surface — the sidebar's Workspace → Snapshots entry
// (docs/design-brief-dark, "Snapshots surface").
//
// The same real restore points as the modal, in the surface idiom, grouped by
// when they were saved. It shares `SnapshotRows` with the modal by construction,
// so the permanent-row semantics (blocky tag, no Remove, per-row restore behind
// its two-step confirm) cannot drift between the two places a person meets them.
//
// The description is the app's REAL promise. The prototype's line —"Addison saves
// a snapshot before every change it makes" — is demo copy and would be a lie
// here: what the core actually guarantees is a restore point before anything
// RISKY, plus the ones you ask for (CLAUDE.md, G3).
//
// Grouping is measured, never guessed: a row is in Today only if its own
// timestamp falls on today's date, "This week" only within the last seven days.
// Empty groups are not rendered at all — an empty "Today" heading would imply
// Addison had nothing to save today, which is a claim, not an absence.

import { Surface, SurfaceRow, SurfaceSection } from "./Surface";
import { SnapshotRows, snapshotsEmptyLine } from "./SnapshotsCard";
import type { SnapshotsState } from "../hooks/useSnapshots";
import type { Snapshot } from "../types/ui";
import type { ReactNode } from "react";

export const SNAPSHOTS_DESCRIPTION =
  "Addison saves a restore point before anything risky, so you can always go back to a setup that worked.";

/** Today / This week / Older — in that order, and only the ones with rows. */
export function groupSnapshotsByRecency(
  snapshots: Snapshot[],
  now: Date = new Date(),
): { label: string; rows: Snapshot[] }[] {
  const today: Snapshot[] = [];
  const week: Snapshot[] = [];
  const older: Snapshot[] = [];
  for (const snap of snapshots) {
    const when = new Date(snap.createdAt * 1000);
    if (Number.isNaN(when.getTime())) {
      older.push(snap);
      continue;
    }
    if (isSameDay(when, now)) today.push(snap);
    else if ((now.getTime() - when.getTime()) / 86_400_000 < 7) week.push(snap);
    else older.push(snap);
  }
  return [
    { label: "Today", rows: today },
    { label: "This week", rows: week },
    { label: "Older", rows: older },
  ].filter((g) => g.rows.length > 0);
}

function isSameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

export function SnapshotsSurface({
  connected,
  snapshots: state,
  pinned,
}: {
  connected: boolean;
  snapshots: SnapshotsState;
  pinned?: ReactNode;
}) {
  const groups = groupSnapshotsByRecency(state.snapshots);
  return (
    <Surface title="Snapshots" description={SNAPSHOTS_DESCRIPTION} pinned={pinned}>
      {state.warning && (
        <SurfaceSection label="Worth knowing">
          <SurfaceRow wrap name={state.warning} />
        </SurfaceSection>
      )}
      {state.notice && (
        <SurfaceSection label="What just happened">
          <SurfaceRow wrap name={state.notice} />
        </SurfaceSection>
      )}
      {groups.length === 0 ? (
        <SurfaceSection label="Restore points">
          <SurfaceRow wrap name={snapshotsEmptyLine(connected, state.snapshotsLoaded)} />
        </SurfaceSection>
      ) : (
        groups.map((group) => (
          <SurfaceSection key={group.label} label={group.label}>
            <SnapshotRows connected={connected} snapshots={state} rows={group.rows} />
          </SurfaceSection>
        ))
      )}
    </Surface>
  );
}
