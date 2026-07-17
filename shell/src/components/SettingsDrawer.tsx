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
import type { CloudModel, LocalSetupState, ProfileState, RoleOption } from "../types/ui";
import type { DiagnosticEntry } from "../ipc/client";
import { RoutineLibrary } from "./RoutineLibrary";
import { LocalModelSetup } from "./LocalModelSetup";

interface Props {
  open: boolean;
  connected: boolean;
  roles: RoleOption[];
  cloudModels: CloudModel[];
  defaultRole: ModelRole;
  defaultCloudModel?: string;
  onChangeDefaultRole: (role: ModelRole) => void;
  onChangeDefaultCloudModel: (modelId: string) => void;
  onSaveKey: (role: string, provider: string, key: string) => Promise<void>;
  localSetup: LocalSetupState | null;
  onStartLocalSetup: (modelId: string) => void;
  /** Profiles (§4.7); null while disconnected or before the core answers. */
  profile: ProfileState | null;
  onSetProfile: (profileId: string) => void;
  /** Developer-only: most recent raw diagnostics, newest first. */
  diagnostics: DiagnosticEntry[];
  onClearDiagnostics: () => void;
  onClose: () => void;
}

export function SettingsDrawer({
  open,
  connected,
  roles,
  cloudModels,
  defaultRole,
  defaultCloudModel,
  onChangeDefaultRole,
  onChangeDefaultCloudModel,
  onSaveKey,
  localSetup,
  onStartLocalSetup,
  profile,
  onSetProfile,
  diagnostics,
  onClearDiagnostics,
  onClose,
}: Props) {
  const [keyValue, setKeyValue] = useState("");
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [saveError, setSaveError] = useState("");

  const configured = roles.filter((r) => r.configured);

  // The persistent default cloud model, resolved the same way the composer
  // picker resolves it: the stored pick if it's still in the catalog, else the
  // catalog's default.
  const cloudValue =
    (defaultCloudModel && cloudModels.some((m) => m.id === defaultCloudModel)
      ? defaultCloudModel
      : (cloudModels.find((m) => m.default) ?? cloudModels[0])?.id) ?? "";
  const cloudDescription = cloudModels.find((m) => m.id === cloudValue)?.description;

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
            className="border border-line bg-surface px-3 py-1.5 text-sm font-medium text-ink-soft hover:border-muted"
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
              className="mt-1 block w-full border border-line bg-surface px-3 py-2.5 text-base text-ink placeholder:text-muted disabled:opacity-60"
            />
            <div className="mt-3 flex items-center gap-3">
              <button
                type="button"
                onClick={saveKey}
                disabled={!connected || !keyValue.trim() || saveState === "saving"}
                className="bg-accent px-4 py-2 text-base font-semibold text-white hover:bg-accent-dark disabled:cursor-not-allowed disabled:opacity-50"
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
                          "flex items-center justify-between border px-4 py-3 text-left text-base " +
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

                {/* Default cloud model — same catalog labels as the picker by the
                    message box, so the two stay consistent. */}
                {cloudModels.length > 0 && (
                  <div className="mt-4">
                    <label
                      htmlFor="default-cloud-model"
                      className="block text-sm font-medium text-ink-soft"
                    >
                      Cloud model
                    </label>
                    <select
                      id="default-cloud-model"
                      value={cloudValue}
                      onChange={(e) => onChangeDefaultCloudModel(e.target.value)}
                      className="mt-1 block w-full border border-line bg-surface px-3 py-2.5 text-base text-ink"
                    >
                      {cloudModels.map((m) => (
                        <option key={m.id} value={m.id}>
                          {m.label}
                        </option>
                      ))}
                    </select>
                    {cloudDescription && (
                      <p className="mt-1 text-sm text-muted">{cloudDescription}</p>
                    )}
                  </div>
                )}
              </>
            )}
          </section>

          {/* Run a model on this computer — the local-model setup flow (§4.1.2). */}
          <LocalModelSetup
            connected={connected}
            roles={roles}
            setup={localSetup}
            onStartSetup={onStartLocalSetup}
          />

          {/* Routines */}
          <section>
            <h3 className="text-base font-semibold text-ink">Routines</h3>
            <div className="mt-2">
              <RoutineLibrary exposeRoutinePlan={profile?.flags.exposeRoutinePlan} />
            </div>
          </section>

          {/* Profile — always last (spec §7.11). Reshapes what's shown, never
              how safety works; the safety-identical wording is the core's own
              per-profile description, rendered verbatim below. */}
          <section>
            <h3 className="text-base font-semibold text-ink">Profile</h3>
            <p className="mt-1 text-sm text-muted">
              A profile changes what Addison shows you — not what it's allowed to
              do, or the permissions it asks for.
            </p>
            {!connected || !profile || profile.profiles.length === 0 ? (
              <p className="mt-2 text-sm text-muted">
                {connected
                  ? "Profile options will appear here in a moment."
                  : "Your profile choices appear here once Addison's engine is connected."}
              </p>
            ) : (
              <>
                <div className="mt-3 flex flex-col gap-2">
                  {profile.profiles.map((p) => {
                    const active = p.id === profile.activeProfile;
                    return (
                      <label
                        key={p.id}
                        className={
                          "flex cursor-pointer gap-3 border px-4 py-3 text-base " +
                          (active
                            ? "border-accent bg-accent-tint"
                            : "border-line bg-surface hover:border-muted")
                        }
                      >
                        <input
                          type="radio"
                          name="addison-profile"
                          value={p.id}
                          checked={active}
                          onChange={() => onSetProfile(p.id)}
                          className="mt-1 h-4 w-4 shrink-0 accent-accent"
                        />
                        <span className="block">
                          <span
                            className={
                              "block font-medium " + (active ? "text-accent-dark" : "text-ink")
                            }
                          >
                            {p.label}
                          </span>
                          {p.description && (
                            <span className="mt-0.5 block text-sm text-muted">
                              {p.description}
                            </span>
                          )}
                        </span>
                      </label>
                    );
                  })}
                </div>

                {/* Headless/CLI hint — Developer only (§7.11). */}
                {profile.flags.headlessCli && (
                  <p className="mt-3 text-sm text-muted">
                    For scripts: Addison's engine speaks JSON-RPC on stdio — run{" "}
                    <code className="font-mono text-xs text-ink-soft">
                      python -m agent_core.main
                    </code>{" "}
                    from the repo.
                  </p>
                )}
              </>
            )}
          </section>

          {/* Diagnostics — Developer only (§7.11). The most recent raw errors,
              kept in a small in-memory ring; empty until something fails. */}
          {profile?.flags.rawDiagnostics && (
            <section>
              <div className="flex items-center justify-between">
                <h3 className="text-base font-semibold text-ink">Diagnostics</h3>
                {diagnostics.length > 0 && (
                  <button
                    type="button"
                    onClick={onClearDiagnostics}
                    className="border border-line bg-surface px-3 py-1.5 text-sm font-medium text-ink-soft hover:border-muted"
                  >
                    Clear
                  </button>
                )}
              </div>
              <p className="mt-1 text-sm text-muted">
                The most recent raw errors from the engine, newest first.
              </p>
              {diagnostics.length === 0 ? (
                <p className="mt-2 text-sm text-muted">Nothing to show yet.</p>
              ) : (
                <ul className="mt-3 space-y-3">
                  {diagnostics.map((d, i) => (
                    <li key={`${d.at}-${i}`} className="border border-line bg-surface p-3">
                      <div className="flex items-baseline justify-between gap-3">
                        <span className="text-sm font-medium text-ink">{d.message}</span>
                        <span className="shrink-0 text-xs text-muted">
                          {new Date(d.at).toLocaleTimeString()}
                        </span>
                      </div>
                      <pre className="mt-1 overflow-x-auto whitespace-pre-wrap font-mono text-xs text-ink-soft">
                        {d.raw}
                      </pre>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          )}
        </div>
      </aside>
    </div>
  );
}
