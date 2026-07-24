// The workspace-trust panel — the Settings face of the coding-harness trust
// boundary (Phase-2 step 5, contract D6). It is shown ONLY on the Developer and
// Custom surfaces (keyed off the active profile, never the policy mode); Simple
// never sees it. That gate lives in SettingsPage.
//
// What trusting a folder does, said out loud here and honestly: inside a trusted
// folder Addison's typed file tools read and edit WITHOUT asking before each
// change — every change is logged and can be undone. Commands Addison runs still
// ask every time. That last sentence is load-bearing: this panel never claims the
// shell is undoable or restore-covered, because it isn't (contract D6 [F2]).
//
// Fern shape rule (docs/design-brief-fern): every control here is rounded, because
// it is the person's to act on, and the fern accent marks the confirm's primary.
// Trusting a folder means Addison will ask LESS often, so — like the Custom guard
// panel — it is gated behind an inline confirm before anything is granted; it is
// never a browser confirm(), which couldn't carry the honest cost line. Revoking
// makes Addison ask first again (a tightening), so it goes straight through.

import { useState } from "react";
import type { WorkspaceCardState } from "../hooks/useWorkspace";

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
    return (
      <p className="text-meta text-muted">
        These settings appear here once Addison&rsquo;s engine is connected.
      </p>
    );
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
    <div>
      <p className="mb-3.5 text-fine leading-relaxed text-ink-soft">{STANDING_LINE}</p>

      {!pendingDir && (
        <button
          type="button"
          disabled={busy}
          onClick={() => void choose()}
          className="rounded-sm bg-fern px-4 py-2 text-meta font-semibold text-on-accent hover:bg-fern-deep disabled:opacity-50 max-md:min-h-[44px]"
        >
          Choose a folder to trust&hellip;
        </button>
      )}

      {/* The grant confirm — names the picked folder before the click, then the
          honest cost line, then the commit. Two-step (pick, then confirm) and
          inline, never window.confirm(). */}
      {pendingDir && (
        <div className="rounded-card bg-fern-tint px-[15px] py-[13px]">
          <p className="font-mono text-label text-ink-soft" data-testid="pending-dir">
            {pendingDir}
          </p>
          <p className="mt-2 text-fine leading-relaxed text-ink-soft">{GRANT_CONFIRM}</p>
          <div className="mt-2.5 flex flex-wrap items-center gap-3">
            <button
              type="button"
              disabled={busy}
              onClick={() => void confirmGrant()}
              className="rounded-pill bg-fern px-[18px] py-[7px] text-xs font-semibold text-on-accent hover:bg-fern-deep disabled:opacity-50"
            >
              Trust this folder
            </button>
            <button
              type="button"
              onClick={() => setPendingDir(null)}
              className="text-xs font-medium text-ink-soft hover:text-muted"
            >
              Not now
            </button>
          </div>
        </div>
      )}

      {/* A refused grant (the data-dir refusal, or a folder that doesn't exist) in
          the core's own already-plain words — never a stack trace. */}
      {error && <p className="mt-3 text-fine leading-relaxed text-ink-soft">{error}</p>}

      {/* The outcome of the last revoke, in plain words. Stays put rather than
          fading — a sentence someone re-reads. */}
      {notice && <p className="mt-3 text-fine leading-relaxed text-ink-soft">{notice}</p>}

      {/* The trusted-roots list, each with a "Stop trusting" control. */}
      <div className="mt-4">
        {!rootsLoaded ? (
          <p className="text-meta text-muted">Looking for your trusted folders&hellip;</p>
        ) : roots.length === 0 ? (
          <p className="text-meta text-muted">
            No trusted folders yet. Choose one above and Addison can work in it without asking
            before each file change.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {roots.map((root) => (
              <li
                key={root.directory}
                className="flex items-start justify-between gap-3 rounded border border-line bg-paper px-[14px] py-2.5"
              >
                <div className="min-w-0 flex-1">
                  <p className="break-all font-mono text-label text-ink">{root.directory}</p>
                  {root.grantedAt ? (
                    <p className="mt-0.5 text-fine text-faint">
                      Trusted {formatWhen(root.grantedAt)}
                    </p>
                  ) : null}
                </div>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void handleRevoke(root.directory)}
                  className="shrink-0 text-xs font-medium text-muted hover:text-danger disabled:opacity-50"
                >
                  Stop trusting
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
