// Settings drawer — the third region (design-doc §7.1). Never required on first
// run; slides in from the right when the user chooses to open it.
//
// Minimal for step 7:
//   - BYOK key entry. The key is WRITE-ONLY from the webview: typed once, handed
//     straight to the Rust shell via `store_provider_key`, and never read back,
//     never displayed, never stored in the frontend (invariant §8.3). We clear
//     the field the moment it's saved.
//   - The persistent default for where Addison thinks (Cloud / On this computer).
//   - A place for the Routines library, which is a step-8 stub.
//
// v1 is Anthropic-only for the cloud key (spec §10); the provider is fixed here
// rather than shown as a chooser.

import { useState } from "react";
import type { ModelRole } from "../types/protocol";
import type { RoleOption } from "../types/ui";
import { RoutineLibrary } from "./RoutineLibrary";

interface Props {
  open: boolean;
  connected: boolean;
  roles: RoleOption[];
  defaultRole: ModelRole;
  onChangeDefaultRole: (role: ModelRole) => void;
  onSaveKey: (role: string, provider: string, key: string) => Promise<void>;
  onClose: () => void;
}

export function SettingsDrawer({
  open,
  connected,
  roles,
  defaultRole,
  onChangeDefaultRole,
  onSaveKey,
  onClose,
}: Props) {
  const [keyValue, setKeyValue] = useState("");
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [saveError, setSaveError] = useState("");

  const configured = roles.filter((r) => r.configured);

  async function saveKey() {
    const trimmed = keyValue.trim();
    if (!trimmed) return;
    setSaveState("saving");
    setSaveError("");
    try {
      await onSaveKey("primary", "anthropic", trimmed);
      // Never keep the key around in the webview once it's handed off.
      setKeyValue("");
      setSaveState("saved");
    } catch (err) {
      setSaveState("error");
      setSaveError(err instanceof Error ? err.message : "Couldn't save the key.");
    }
  }

  return (
    <div
      className={
        "fixed inset-0 z-20 " + (open ? "" : "pointer-events-none")
      }
      aria-hidden={!open}
    >
      {/* Scrim */}
      <div
        onClick={onClose}
        className={
          "absolute inset-0 bg-ink/20 transition-opacity " +
          (open ? "opacity-100" : "opacity-0")
        }
      />

      {/* Panel */}
      <aside
        role="dialog"
        aria-label="Settings"
        className={
          "absolute right-0 top-0 flex h-full w-full max-w-md flex-col bg-paper shadow-drawer transition-transform " +
          (open ? "translate-x-0" : "translate-x-full")
        }
      >
        <header className="flex items-center justify-between border-b border-line px-6 py-4">
          <h2 className="text-lg font-semibold text-ink">Settings</h2>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-line bg-surface px-3 py-1.5 text-sm font-medium text-ink-soft hover:border-muted"
          >
            Close
          </button>
        </header>

        <div className="min-h-0 flex-1 space-y-8 overflow-y-auto px-6 py-6">
          {/* Your own key */}
          <section>
            <h3 className="text-base font-semibold text-ink">Use your own key</h3>
            <p className="mt-1 text-sm text-muted">
              Paste a key to run Addison on your own account. It's stored safely
              on this computer and never shown again — not even here.
            </p>
            <label className="mt-3 block text-sm font-medium text-ink-soft" htmlFor="byok-key">
              Anthropic key
            </label>
            <input
              id="byok-key"
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={keyValue}
              onChange={(e) => {
                setKeyValue(e.target.value);
                if (saveState !== "idle") setSaveState("idle");
              }}
              placeholder="Paste your key here"
              disabled={!connected || saveState === "saving"}
              className="mt-1 block w-full rounded-lg border border-line bg-surface px-3 py-2.5 text-base text-ink placeholder:text-muted disabled:opacity-60"
            />
            <div className="mt-3 flex items-center gap-3">
              <button
                type="button"
                onClick={saveKey}
                disabled={!connected || !keyValue.trim() || saveState === "saving"}
                className="rounded-lg bg-accent px-4 py-2 text-base font-semibold text-white hover:bg-accent-dark disabled:cursor-not-allowed disabled:opacity-50"
              >
                {saveState === "saving" ? "Saving…" : "Save key"}
              </button>
              {saveState === "saved" && (
                <span className="text-sm text-accent-dark">Saved securely.</span>
              )}
              {saveState === "error" && (
                <span className="text-sm text-danger">{saveError}</span>
              )}
            </div>
            {!connected && (
              <p className="mt-2 text-sm text-muted">
                You can add a key once Addison's engine is connected.
              </p>
            )}
          </section>

          {/* Where Addison thinks — persistent default */}
          <section>
            <h3 className="text-base font-semibold text-ink">Where Addison thinks</h3>
            {configured.length === 0 ? (
              <p className="mt-1 text-sm text-muted">
                Nothing is set up yet. Add your own key above, or Addison's guided
                setup will help you when you start chatting.
              </p>
            ) : (
              <>
                <p className="mt-1 text-sm text-muted">
                  Choose the default. You can still switch for a single message
                  from the box where you type.
                </p>
                <div className="mt-3 flex flex-col gap-2">
                  {configured.map((r) => {
                    const active = r.role === defaultRole;
                    return (
                      <button
                        key={r.role}
                        type="button"
                        onClick={() => onChangeDefaultRole(r.role)}
                        aria-pressed={active}
                        className={
                          "flex items-center justify-between rounded-lg border px-4 py-3 text-left text-base " +
                          (active
                            ? "border-accent bg-accent-tint text-accent-dark"
                            : "border-line bg-surface text-ink hover:border-muted")
                        }
                      >
                        <span>{r.label}</span>
                        {active && <span aria-hidden="true">✓</span>}
                      </button>
                    );
                  })}
                </div>
              </>
            )}
          </section>

          {/* Routines — step-8 stub */}
          <section>
            <h3 className="text-base font-semibold text-ink">Routines</h3>
            <div className="mt-2">
              <RoutineLibrary />
            </div>
          </section>
        </div>
      </aside>
    </div>
  );
}
