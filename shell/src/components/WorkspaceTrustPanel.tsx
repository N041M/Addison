// The workspace-trust panel — the Settings face of the coding-harness trust
// boundary (Phase-2 step 5, contract D6), in the dark direction's row idiom. It
// is shown ONLY on the Developer and Custom surfaces (keyed off the active
// profile, never the policy mode); Simple never sees it. That gate lives in
// SettingsPage.
//
// What trusting a folder does, said out loud here and honestly: inside a trusted
// folder Addison's typed file tools read and edit WITHOUT asking before each
// change — every change is logged and can be undone. Commands Addison runs still
// ask every time. That last sentence is load-bearing: this panel never claims the
// shell is undoable or restore-covered, because it isn't (contract D6 [F2]).
//
// Trusting a folder means Addison will ask LESS often, so — like the Custom guard
// panel — it is gated behind an inline two-step confirm before anything is
// granted; it is never a browser confirm(), which couldn't carry the honest cost
// line. Revoking makes Addison ask first again (a tightening), so it goes
// straight through.

import { useState } from "react";
import type { WorkspaceCardState } from "../hooks/useWorkspace";
import { RowConfirm, SurfaceRow } from "./Surface";

// --- Frozen copy (contract D6) — byte-for-byte. -----------------------------

/** The card's standing explanatory line. HONEST about the shipped substrate:
 * typed file edits are logged + undoable; commands still ask every time. Do NOT
 * add any claim that shell commands are undoable or restore-covered. */
const STANDING_LINE =
  "Inside a trusted folder, Addison reads and edits files without asking first — " +
  "each change is logged and can be undone. Commands it runs still ask every time.";

/** Shown in the inline confirm after a folder is picked, before trust is granted.
 * Names what changes (Addison stops asking before each file change) and that it is
 * logged, then asks. grantTrust fires only when the person confirms. */
const GRANT_CONFIRM =
  "While Addison works in this folder it won't ask before each file change, and " +
  "everything is logged. Trust this folder?";

/** The accent action that opens the OS folder picker. */
const CHOOSE_ACTION = "choose a folder…";

function formatWhen(grantedAt?: number): string {
  if (!grantedAt) return "";
  try {
    return new Date(grantedAt * 1000).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });
  } catch {
    return "";
  }
}

export function WorkspaceTrustPanel({
  connected,
  workspace: state,
}: {
  connected: boolean;
  workspace: WorkspaceCardState;
}) {
  // The folder picked and awaiting the grant confirm, held until the person
  // confirms or backs out. Inline, never a browser confirm() — a native dialog
  // can't carry the honest cost line above. Its presence IS the second step:
  // picking a folder is step one, confirming here is step two, and grantTrust
  // fires only from the confirm.
  const [pendingDir, setPendingDir] = useState<string | null>(null);

  const { roots, rootsLoaded, busy, error, notice, pickDirectory, handleGrant, handleRevoke } =
    state;

  if (!connected) {
    return <SurfaceRow wrap name="These settings appear here once Addison's engine is connected." />;
  }

  async function choose() {
    const dir = await pickDirectory();
    // A cancelled (or unavailable) picker returns null — do nothing, don't open
    // the confirm on a folder that was never chosen.
    if (dir) setPendingDir(dir);
  }

  async function confirmGrant() {
    if (!pendingDir) return;
    const ok = await handleGrant(pendingDir);
    // Close the confirm only on success; a refusal (e.g. the data-dir refusal)
    // leaves the panel so the person sees the plain error line and can pick a
    // different folder.
    if (ok) setPendingDir(null);
  }

  return (
    <>
      <SurfaceRow name={STANDING_LINE} />

      {/* The grant confirm — names the picked folder before the click, then the
          honest cost line, then the commit. Two-step (pick, then confirm) and
          inline, never window.confirm(). */}
      {pendingDir && (
        <SurfaceRow
          name={
            <span data-testid="pending-dir" className="break-all font-mono text-[11px] text-ink">
              {pendingDir}
            </span>
          }
        >
          <RowConfirm
            busy={busy}
            confirmLabel="Trust this folder"
            onConfirm={() => void confirmGrant()}
            onCancel={() => setPendingDir(null)}
          >
            {GRANT_CONFIRM}
          </RowConfirm>
        </SurfaceRow>
      )}

      {/* A refused grant (the data-dir refusal, or a folder that doesn't exist) in
          the core's own already-plain words — never a stack trace. */}
      {error && <SurfaceRow name={error} />}

      {/* The outcome of the last revoke, in plain words. Stays put rather than
          fading — a sentence someone re-reads. */}
      {notice && <SurfaceRow name={notice} />}

      {!rootsLoaded ? (
        <SurfaceRow wrap name="Looking for your trusted folders…" />
      ) : roots.length === 0 ? (
        <SurfaceRow
          name="No trusted folders yet"
          value="Addison asks before each file change"
          action={pendingDir ? undefined : CHOOSE_ACTION}
          actionDisabled={busy}
          onAction={pendingDir ? undefined : () => void choose()}
        />
      ) : (
        <>
          {roots.map((root) => (
            <SurfaceRow
              key={root.directory}
              name={
                <span className="break-all font-mono text-[11px] text-ink-soft">
                  {root.directory}
                </span>
              }
              value={root.grantedAt ? `trusted ${formatWhen(root.grantedAt)}` : undefined}
              action="Stop trusting"
              actionAriaLabel={`Stop trusting ${root.directory}`}
              actionTone="danger"
              actionDisabled={busy}
              onAction={() => void handleRevoke(root.directory)}
            />
          ))}
          {!pendingDir && (
            <SurfaceRow
              name="Another folder"
              action={CHOOSE_ACTION}
              actionDisabled={busy}
              onAction={() => void choose()}
            />
          )}
        </>
      )}
    </>
  );
}
