// The workspace-trust panel — the Settings face of the coding-harness trust
// boundary (Phase-2 step 5, contract D6), in the dark direction's row idiom.
//
// SHOWN IN EVERY PROFILE SINCE 2026-08-12 (owner decision). It was Developer/
// Custom only until then, which stopped making sense on 2026-08-11: Simple gained
// the two path-bounded file tools, and those scope by TRUSTED ROOT — so a
// Simple-only person could not grant the one thing their own file tools need, and
// reached a trusted folder only if they had once been in Developer.
// docs/SAFETY.md invariant 1 owns that decision and this follow-up. The gate that
// remains lives in SettingsPage: the section renders once the profile has loaded.
//
// What trusting a folder does is said out loud here, and it is NOT the same
// sentence in both policy modes — which is why the copy below comes in two:
//
//   OPEN (Developer/Custom) — inside a trusted folder Addison's typed file tools
//     read and edit WITHOUT asking before each change; every change is logged and
//     can be undone, and commands Addison runs still ask every time. That last
//     clause is load-bearing: this panel never claims the shell is undoable or
//     restore-covered, because it isn't (contract D6 [F2]).
//   SAFE (Simple) — a destructive call cards PER INVOCATION, so Addison asks
//     before every single change (permissions/gate.py). Simple runs no commands at
//     all, so the sentence about commands would be describing an ability the
//     profile does not have.
//
// ONE STRING FOR BOTH WAS TRIED AND REJECTED: any wording true of both modes has
// to say "it depends which profile you are in", which is exactly the sentence a
// Settings panel exists to spare Mira and Petr. So the copy is per-mode, keyed off
// the policy mode (which is derived 1:1 from the profile) rather than the profile
// name — the mode is what actually decides whether the card comes first.
//
// Trusting a folder widens what Addison may touch in EITHER mode, so — like the
// Custom guard panel — it is gated behind an inline two-step confirm before
// anything is granted; it is never a browser confirm(), which couldn't carry the
// honest line. Neither step is skipped or softened in any profile. Revoking is a
// tightening, so it goes straight through.

import { useState } from "react";
import type { WorkspaceCardState } from "../hooks/useWorkspace";
import { RowConfirm, SurfaceRow } from "./Surface";

// --- Frozen copy (contract D6) — byte-for-byte. -----------------------------

/** The card's standing explanatory line in OPEN. HONEST about the shipped
 * substrate: typed file edits are logged + undoable; commands still ask every
 * time. Do NOT add any claim that shell commands are undoable or restore-covered. */
const STANDING_LINE =
  "Inside a trusted folder, Addison reads and edits files without asking first — " +
  "each change is logged and can be undone. Commands it runs still ask every time.";

/** The same line for SAFE, where the truth is the opposite one: every change is
 * announced by a card that names the file, before it happens. No sentence about
 * commands — Simple runs none. */
const STANDING_LINE_ASKS =
  "Inside a trusted folder, Addison can read your files and help you change them. " +
  "It asks you before every change, and every change can be undone.";

/** Shown in the inline confirm after a folder is picked, before trust is granted.
 * Names what changes (Addison stops asking before each file change) and that it is
 * logged, then asks. grantTrust fires only when the person confirms. */
const GRANT_CONFIRM =
  "While Addison works in this folder it won't ask before each file change, and " +
  "everything is logged. Trust this folder?";

/** The SAFE confirm. What trusting costs here is not the asking — that stays — it
 * is that Addison can open and change the things in this folder at all. */
const GRANT_CONFIRM_ASKS =
  "Addison will be able to open the files in this folder, and it will ask you " +
  "before every change it makes. Trust this folder?";

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
  asksBeforeEachChange,
}: {
  connected: boolean;
  workspace: WorkspaceCardState;
  /** SAFE (Simple) vs OPEN (Developer/Custom), from `profile.mode` — it picks the
   * copy, and nothing else. TRUE is the SAFE story: a card before every change.
   * REQUIRED, deliberately: a default would decide which of two honest sentences
   * a person reads, and getting it wrong in the TRUE direction would promise an
   * "it asks first" that OPEN does not give. */
  asksBeforeEachChange: boolean;
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
      <SurfaceRow name={asksBeforeEachChange ? STANDING_LINE_ASKS : STANDING_LINE} />

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
            {asksBeforeEachChange ? GRANT_CONFIRM_ASKS : GRANT_CONFIRM}
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
          // What the empty state COSTS, which differs by mode. In OPEN the answer
          // is that Addison asks first; in SAFE it asks first either way, so the
          // honest answer there is the other one — with no trusted folder Addison
          // can only work on a file you hand it through the picker.
          value={
            asksBeforeEachChange
              ? "Addison can only open files you pick for it"
              : "Addison asks before each file change"
          }
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
